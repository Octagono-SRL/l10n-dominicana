from odoo import models, api, fields, Command, _
from odoo.exceptions import UserError


class AccountMoveReversal(models.TransientModel):
    _inherit = "account.move.reversal"

    @api.model
    def _get_refund_type_selection(self):
        selection = [
            ("full_refund", _("Full Refund")),
            ("percentage", _("Percentage")),
            ("fixed_amount", _("Amount")),
        ]

        return selection

    @api.model
    def _get_default_refund_type(self):
        return "full_refund"

    @api.model
    def _get_refund_action_selection(self):
        return [
            ("draft_refund", _("Partial Refund")),
            ("apply_refund", _("Full Refund")),
        ]

    @api.model
    def _default_account(self):
        move_type = self._context.get("move_type")
        journal = (
            self.env["account.move"]
            .with_context(
                default_type=move_type, default_company_id=self.env.company.id
            )
            ._get_default_journal()
        )
        if move_type in ("out_invoice", "in_refund"):
            return journal.default_credit_account_id.id
        return journal.default_debit_account_id.id

    country_code = fields.Char(
        related="company_id.country_code",
        help="Technical field used to hide/show fields regarding the localization",
    )
    refund_type = fields.Selection(
        selection=_get_refund_type_selection,
        default=_get_default_refund_type,
    )
    refund_action = fields.Selection(
        selection=_get_refund_action_selection,
        default="draft_refund",
        string="Refund Action",
    )
    percentage = fields.Float()
    amount = fields.Float()
    l10n_do_ecf_modification_code = fields.Selection(
        selection=lambda self: self.env[
            "account.move"
        ]._get_l10n_do_ecf_modification_code(),
        string="e-CF Modification Code",
        copy=False,
    )
    is_ecf_invoice = fields.Boolean(
        string="Is Electronic Invoice",
    )
    l10n_do_is_ecf_note = fields.Boolean(
        compute="_compute_l10n_do_is_ecf_note",
        help="Technical field: the note gets an electronic (e-CF) document "
        "type, so it needs the e-CF modification code, also when the "
        "supplier issued it and its number is typed by hand.",
    )

    @api.depends("l10n_latam_document_type_id")
    def _compute_l10n_do_is_ecf_note(self):
        for wizard in self:
            ncf_type = wizard.l10n_latam_document_type_id.l10n_do_ncf_type or ""
            wizard.l10n_do_is_ecf_note = ncf_type.startswith("e-")

    @api.depends(
        "l10n_latam_document_type_id", "country_code", "l10n_latam_use_documents"
    )
    def _compute_l10n_latam_manual_document_number(self):
        self.l10n_latam_manual_document_number = False
        l10n_do_recs = self.filtered(
            lambda r: r.move_ids
            and r.l10n_latam_use_documents
            and r.country_code == "DO"
        )
        for rec in l10n_do_recs:
            move = rec.move_ids[0]
            rec.l10n_latam_manual_document_number = (
                move.l10n_latam_manual_document_number
            )

        super(
            AccountMoveReversal, self - l10n_do_recs
        )._compute_l10n_latam_manual_document_number()

    @api.model
    def default_get(self, fields):
        res = super(AccountMoveReversal, self).default_get(fields)
        move_ids = (
            self.env["account.move"].browse(self.env.context["active_ids"])
            if self.env.context.get("active_model") == "account.move"
            else self.env["account.move"]
        )
        move_ids_use_document = move_ids.filtered(
            lambda move: move.l10n_latam_use_documents
            and move.company_id.country_code == "DO"
        )

        if len(move_ids_use_document) > 1:
            raise UserError(
                _(
                    "You cannot create Credit Notes from multiple "
                    "documents at a time."
                )
            )
        if move_ids_use_document:
            res["is_ecf_invoice"] = move_ids_use_document[
                0
            ].company_id.l10n_do_ecf_issuer

        return res

    def _l10n_do_partial_refund_lines(self, move):
        """Lines of a Percentage/Amount credit note.

        Like l10n_do 14: one line named after the reason, quantity 1, priced
        at the percentage of the invoice untaxed amount (or the amount typed,
        before taxes). It now keeps the invoice taxes (DGII: a credit note
        must carry its share of the ITBIS), so an invoice mixing taxes gives
        one line per account and taxes, each with its share of the amount.
        """
        groups = {}
        for line in move.invoice_line_ids.filtered(
            lambda l: l.display_type == "product"
        ):
            key = (line.account_id, line.tax_ids)
            group = groups.setdefault(key, {"subtotal": 0.0, "price": 0.0})
            group["subtotal"] += line.price_subtotal
            # with price-included taxes the unit price carries the tax
            group["price"] += (
                line.price_total
                if any(line.tax_ids.mapped("price_include"))
                else line.price_subtotal
            )
        untaxed = sum(g["subtotal"] for g in groups.values())
        if self.refund_type == "percentage":
            ratio = (self.percentage or 0.0) / 100.0
        else:
            ratio = (self.amount or 0.0) / untaxed if untaxed else 0.0

        currency = move.currency_id
        name = self.reason or _("Refund")
        commands = []
        left = self.amount or 0.0
        keys = list(groups)
        for index, key in enumerate(keys):
            account, taxes = key
            group = groups[key]
            price = currency.round(group["price"] * ratio)
            if self.refund_type == "fixed_amount" and group["price"] == group["subtotal"]:
                if index == len(keys) - 1:
                    price = currency.round(left)  # no cent lost to rounding
                left -= price
            commands.append(
                Command.create(
                    {
                        "name": name,
                        "quantity": 1,
                        "price_unit": price,
                        "account_id": account.id,
                        "tax_ids": [Command.set(taxes.ids)],
                    }
                )
            )
        if not commands:
            commands.append(
                Command.create(
                    {
                        "name": name,
                        "quantity": 1,
                        "price_unit": currency.round(
                            self.amount
                            if self.refund_type == "fixed_amount"
                            else move.amount_untaxed * ratio
                        ),
                    }
                )
            )
        return commands

    def _prepare_default_reversal(self, move):
        res = super()._prepare_default_reversal(move)
        if move.country_code != "DO":
            return res

        # Odoo 17 reverses by copy(), so the old _reverse_move_vals hook never
        # runs: origin NCF, modification code and the partial line go here.
        # The origin is the NCF/e-NCF of the invoice (l10n_do_fiscal_number,
        # like l10n_do_dgii_factura_electronica did), the reference otherwise.
        res["l10n_do_origin_ncf"] = move.l10n_do_fiscal_number or move.ref
        res["l10n_do_ecf_modification_code"] = self.l10n_do_ecf_modification_code
        if self.refund_type in ("percentage", "fixed_amount"):
            res["line_ids"] = self._l10n_do_partial_refund_lines(move)
        return res

    def reverse_moves(self, is_modify=False):
        self.ensure_one()
        is_partial = self.country_code == "DO" and self.refund_type in (
            "percentage",
            "fixed_amount",
        )
        apply_refund = is_partial and self.refund_action == "apply_refund"
        if is_partial:
            # A partial credit note never cancels the invoice nor re-creates
            # it. Full refunds keep the button the user clicked: "Reverse" or
            # "Reverse and Create Invoice" (refund_method refund/modify in 14).
            is_modify = False
        action = super(
            AccountMoveReversal,
            self.with_context(
                refund_type=self.refund_type,
                percentage=self.percentage,
                amount=self.amount,
                reason=self.reason,
                l10n_do_ecf_modification_code=self.l10n_do_ecf_modification_code,
            ),
        ).reverse_moves(is_modify=is_modify)
        if apply_refund:
            self._l10n_do_apply_refunds(self.new_move_ids)
        return action

    def _l10n_do_apply_refunds(self, refunds):
        """'Full Refund' action on a partial credit note (refund_method
        'cancel' in 14): post the credit note and reconcile it with the
        invoice. Payments already reconciled with the invoice stay as they are.
        """
        refunds = refunds.filtered(
            lambda m: m.state == "draft" and m.auto_post == "no"
        )
        refunds.action_post()
        for refund in refunds:
            invoice = refund.reversed_entry_id
            if not invoice or invoice.state != "posted":
                continue
            for line in refund.line_ids.filtered(
                lambda l: l.account_id.account_type
                in ("asset_receivable", "liability_payable")
                and not l.reconciled
            ):
                invoice.js_assign_outstanding_line(line.id)

    @api.depends("move_ids", "journal_id")
    def _compute_document_type(self):
        self.l10n_latam_available_document_type_ids = False
        self.l10n_latam_document_type_id = False
        self.l10n_latam_use_documents = False
        do_wizard = self.filtered(
            lambda w: w.journal_id
            and w.journal_id.l10n_latam_use_documents
            and w.country_code == "DO"
        )
        for record in do_wizard:
            if len(record.move_ids) > 1:
                move_ids_use_document = record.move_ids._origin.filtered(
                    lambda move: move.l10n_latam_use_documents
                )
                if move_ids_use_document:
                    raise UserError(
                        _(
                            "You can only reverse documents with legal invoicing documents from Latin America "
                            "one at a time.\nProblematic documents: %s"
                        )
                        % ", ".join(move_ids_use_document.mapped("name"))
                    )
            else:
                record.l10n_latam_use_documents = (
                    record.journal_id.l10n_latam_use_documents
                )

            if record.l10n_latam_use_documents:
                refund = record.env["account.move"].new(
                    {
                        "move_type": record._reverse_type_map(
                            record.move_ids.move_type
                        ),
                        "journal_id": record.journal_id.id,
                        "partner_id": record.move_ids.partner_id.id,
                        "company_id": record.move_ids.company_id.id,
                    }
                )
                record.l10n_latam_document_type_id = refund.l10n_latam_document_type_id
                record.l10n_latam_available_document_type_ids = (
                    refund.l10n_latam_available_document_type_ids
                )
        super(AccountMoveReversal, self - do_wizard)._compute_document_type()
