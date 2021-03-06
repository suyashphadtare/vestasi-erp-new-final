# Copyright (c) 2013, Web Notes Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import flt, cint
from frappe import _
from frappe.utils import cstr, flt, getdate, comma_and,cint

from frappe.model.document import Document

class PackingSlip(Document):

	def validate(self):
		"""
			* Validate existence of submitted Delivery Note
			* Case nos do not overlap
			* Check if packed qty doesn't exceed actual qty of delivery note

			It is necessary to validate case nos before checking quantity
		"""
		#self.validate_delivery_note()
		self.validate_items_mandatory()
		self.validate_case_nos()
		self.validate_qty()
		self.get_address()

		from erpnext.utilities.transaction_base import validate_uom_is_integer
		validate_uom_is_integer(self, "stock_uom", "qty")
		validate_uom_is_integer(self, "weight_uom", "net_weight")

	def get_address(self):
		self.delivery_address=frappe.db.get_value("Delivery Note",self.delivery_note, "address_display")

	

	def validate_delivery_note(self):
		"""
			Validates if delivery note has status as draft
		"""
		if cint(frappe.db.get_value("Delivery Note", self.delivery_note, "docstatus")) != 0:
			frappe.throw(_("Delivery Note {0} must not be submitted").format(self.delivery_note))

	def validate_items_mandatory(self):
		rows = [d.item_code for d in self.get("item_details")]
		if not rows:
			frappe.msgprint(_("No Items to pack"), raise_exception=1)

	def validate_case_nos(self):
		"""
			Validate if case nos overlap. If they do, recommend next case no.
		"""
		if not cint(self.from_case_no):
			frappe.msgprint(_("Please specify a valid 'From Case No.'"), raise_exception=1)
		elif not self.to_case_no:
			self.to_case_no = self.from_case_no
		elif self.from_case_no > self.to_case_no:
			frappe.msgprint(_("'To Case No.' cannot be less than 'From Case No.'"),
				raise_exception=1)


		res = frappe.db.sql("""SELECT name FROM `tabPacking Slip`
			WHERE delivery_note = %(delivery_note)s AND docstatus = 1 AND
			((from_case_no BETWEEN %(from_case_no)s AND %(to_case_no)s)
			OR (to_case_no BETWEEN %(from_case_no)s AND %(to_case_no)s)
			OR (%(from_case_no)s BETWEEN from_case_no AND to_case_no))
			""", {"delivery_note":self.delivery_note,
				"from_case_no":self.from_case_no,
				"to_case_no":self.to_case_no})

		if res:
			frappe.throw(_("""Case No(s) already in use. Try from Case No {0}""").format(self.get_recommended_case_no()))

	def validate_qty(self):
		"""
			Check packed qty across packing slips and delivery note
		"""
		# Get Delivery Note Items, Item Quantity Dict and No. of Cases for this Packing slip
		dn_details, ps_item_qty, no_of_cases = self.get_details_for_packing()

		for item in dn_details:
			new_packed_qty = (flt(ps_item_qty[item['item_code']]) * no_of_cases) + \
			 	flt(item['packed_qty'])
			if new_packed_qty > flt(item['qty']) and no_of_cases:
				self.recommend_new_qty(item, ps_item_qty, no_of_cases)


	def get_details_for_packing(self):
		"""
			Returns
			* 'Delivery Note Items' query result as a list of dict
			* Item Quantity dict of current packing slip doc
			* No. of Cases of this packing slip
		"""

		rows = [d.item_code for d in self.get("item_details")]

		condition = ""
		if rows:
			condition = " and item_code in (%s)" % (", ".join(["%s"]*len(rows)))

		# gets item code, qty per item code, latest packed qty per item code and stock uom
		res = frappe.db.sql("""select item_code, ifnull(sum(qty), 0) as qty,
			(select sum(ifnull(psi.qty, 0) * (abs(ps.to_case_no - ps.from_case_no) + 1))
				from `tabPacking Slip` ps, `tabPacking Slip Item` psi
				where ps.name = psi.parent and ps.docstatus = 1
				and ps.delivery_note = dni.parent and psi.item_code=dni.item_code) as packed_qty,
			stock_uom, item_name
			from `tabDelivery Note Item` dni
			where parent=%s %s
			group by item_code""" % ("%s", condition),
			tuple([self.delivery_note] + rows), as_dict=1)

		ps_item_qty = dict([[d.item_code, d.qty] for d in self.get("item_details")])
		no_of_cases = cint(self.to_case_no) - cint(self.from_case_no) + 1

		return res, ps_item_qty, no_of_cases


	def recommend_new_qty(self, item, ps_item_qty, no_of_cases):
		"""
			Recommend a new quantity and raise a validation exception
		"""
		item['recommended_qty'] = (flt(item['qty']) - flt(item['packed_qty'])) / no_of_cases
		item['specified_qty'] = flt(ps_item_qty[item['item_code']])
		if not item['packed_qty']: item['packed_qty'] = 0

		frappe.throw(_("Quantity for Item {0} must be less than {1}").format(item.get("item_code"), item.get("recommended_qty")))

	def update_item_details(self):
		"""
			Fill empty columns in Packing Slip Item
		"""
		if not self.from_case_no:
			self.from_case_no = self.get_recommended_case_no()

		for d in self.get("item_details"):
			res = frappe.db.get_value("Item", d.item_code,
				["net_weight", "weight_uom"], as_dict=True)

			if res and len(res)>0:
				d.net_weight = res["net_weight"]
				d.weight_uom = res["weight_uom"]

	def get_recommended_case_no(self):
		"""
			Returns the next case no. for a new packing slip for a delivery
			note
		"""
		recommended_case_no = frappe.db.sql("""SELECT MAX(to_case_no) FROM `tabPacking Slip`
			WHERE delivery_note = %s AND docstatus=1""", self.delivery_note)

		return cint(recommended_case_no[0][0]) + 1

	def get_items(self):
		self.set("item_details", [])

		dn_details = self.get_details_for_packing()[0]
		for item in dn_details:
			if flt(item.qty) > flt(item.packed_qty):
				ch = self.append('item_details', {})
				ch.item_code = item.item_code
				ch.item_name = item.item_name
				ch.stock_uom = item.stock_uom
				ch.qty = flt(item.qty) - flt(item.packed_qty)
		self.update_item_details()

def item_details(doctype, txt, searchfield, start, page_len, filters):
	from erpnext.controllers.queries import get_match_cond
	return frappe.db.sql("""select name, item_name, description from `tabItem`
				where name in ( select item_code FROM `tabDelivery Note Item`
	 						where parent= %s)
	 			and %s like "%s" %s
	 			limit  %s, %s """ % ("%s", searchfield, "%s",
	 			get_match_cond(doctype), "%s", "%s"),
	 			((filters or {}).get("delivery_note"), "%%%s%%" % txt, start, page_len))

def delivery_note_details(doctype, txt, searchfield, start, page_len, filters):
	return frappe.db.sql(""" select name from `tabDelivery Note` where docstatus != 2  """)

@frappe.whitelist()
def get_customer_info(delivery_no):
	frappe.errprint("In get customer info")
	frappe.errprint(delivery_no)
	customer_info=frappe.db.sql("select po_no,ref_no from `tabDelivery Note` where name='%s'"%(delivery_no),debug=True,as_list=1)
	frappe.errprint(customer_info)
	return customer_info

@frappe.whitelist()
def get_address(address):
	cust_address=frappe.db.get_value('Address',{"name":address,"is_primary_address":1},"*")
	if cust_address:
		address=cstr(cust_address["address_line1"])+'</br>'+cstr(cust_address["address_line2"])+'</br>'+cstr(cust_address["city"])+'</br>'+cstr(cust_address["state"])+'</br>Phone:'+cstr(cust_address["phone"])+'</br>Fax:'+cstr(cust_address["fax"])
		frappe.errprint(address)
		return address

@frappe.whitelist()
def get_terms(terms_nm):
	terms=frappe.db.get_value('Terms and Conditions',{"name":terms_nm},'terms')
	if terms:
		return terms
