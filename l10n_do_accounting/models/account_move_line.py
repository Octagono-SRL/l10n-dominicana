from odoo import models, fields


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    l10n_do_itbis_amount = fields.Monetary(
        string="ITBIS Amount",
        store=True,
        readonly=True,
        currency_field="currency_id",
    )

    def _get_price_total_and_subtotal(
        self,
        price_unit=None,
        quantity=None,
        discount=None,
        currency=None,
        product=None,
        partner=None,
        taxes=None,
        move_type=None,
    ):
        self.ensure_one()
        res = super(AccountMoveLine, self)._get_price_total_and_subtotal(
            price_unit=price_unit,
            quantity=quantity,
            discount=discount,
            currency=currency,
            product=product,
            partner=partner,
            taxes=taxes,
            move_type=move_type,
        )

        if self.move_id.is_ecf_invoice:
            # See account_move.py's _l10n_do_tax_group for why this can't
            # just be self.env.ref("l10n_do.tax_group_itbis") -- that
            # xmlid isn't guaranteed to survive an OpenUpgrade migration
            # even though the underlying account.tax.group row does.
            itbis_group = self.env.ref(
                "l10n_do.tax_group_itbis", raise_if_not_found=False
            ) or self.env["account.tax.group"].search(
                [("name", "=", "ITBIS")], limit=1
            )
            line_itbis_taxes = self.tax_ids.filtered(
                lambda t: t.tax_group_id == itbis_group
            )
            price_unit = self.price_unit
            if self.discount:
                price_unit = price_unit - (price_unit * (self.discount / 100))
            itbis_taxes_data = line_itbis_taxes.compute_all(
                price_unit=price_unit,
                quantity=self.quantity,
            )
            res["l10n_do_itbis_amount"] = sum(
                [t["amount"] for t in itbis_taxes_data["taxes"]]
            )
        return res
