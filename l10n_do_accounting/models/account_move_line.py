from odoo import api, fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    l10n_do_itbis_amount = fields.Monetary(
        string="ITBIS Amount",
        compute="_compute_l10n_do_itbis_amount",
        store=True,
        readonly=True,
        currency_field="currency_id",
    )

    @api.depends(
        "move_id.is_ecf_invoice",
        "tax_ids",
        "price_unit",
        "quantity",
        "discount",
        "display_type",
    )
    def _compute_l10n_do_itbis_amount(self):
        for line in self:
            amount = 0.0
            if line.display_type == "product" and line.move_id.is_ecf_invoice:
                itbis_group = line.move_id._l10n_do_tax_group("ITBIS")
                itbis_taxes = line.tax_ids.filtered(
                    lambda t: t.tax_group_id == itbis_group
                )
                price_unit = line.price_unit * (1 - (line.discount or 0.0) / 100)
                taxes_data = itbis_taxes.compute_all(
                    price_unit=price_unit, quantity=line.quantity
                )
                amount = sum(t["amount"] for t in taxes_data["taxes"])
            line.l10n_do_itbis_amount = amount
