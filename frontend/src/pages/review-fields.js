export const INVOICE_FIELDS = [
  { path: "/invoice_number", label: "Invoice number" },
  { path: "/invoice_date", label: "Invoice date" },
  { path: "/due_date", label: "Due date" },
  { path: "/currency", label: "Currency" },
  { path: "/place_of_supply", label: "Place of supply" },
  { path: "/reverse_charge", label: "Reverse charge", type: "boolean" },
]

export const PARTY_FIELDS = [
  { key: "name", label: "Name" },
  { key: "gstin", label: "GSTIN" },
  { key: "pan", label: "PAN" },
  { key: "address", label: "Address", multiline: true },
  { key: "state_code", label: "State code" },
]

export const TOTAL_FIELDS = [
  { key: "taxable_amount", label: "Taxable amount" },
  { key: "discount_amount", label: "Discount" },
  { key: "cgst_amount", label: "CGST" },
  { key: "sgst_amount", label: "SGST" },
  { key: "igst_amount", label: "IGST" },
  { key: "cess_amount", label: "Cess" },
  { key: "round_off", label: "Round off" },
  { key: "grand_total", label: "Grand total" },
]

export const LINE_ITEM_FIELDS = [
  { key: "description", label: "Description", multiline: true },
  { key: "hsn_sac", label: "HSN/SAC" },
  { key: "quantity", label: "Quantity" },
  { key: "unit", label: "Unit" },
  { key: "unit_price", label: "Unit price" },
  { key: "discount", label: "Discount" },
  { key: "taxable_value", label: "Taxable value" },
  { key: "gst_rate", label: "GST rate" },
  { key: "total", label: "Total" },
]

export const LINE_TAX_FIELDS = [
  { key: "cgst", label: "CGST" },
  { key: "sgst", label: "SGST" },
  { key: "igst", label: "IGST" },
  { key: "cess", label: "Cess" },
]

export function valueAtPointer(data, pointer) {
  return pointer
    .split("/")
    .slice(1)
    .reduce(
      (value, segment) =>
        value?.[segment.replaceAll("~1", "/").replaceAll("~0", "~")],
      data
    )
}
