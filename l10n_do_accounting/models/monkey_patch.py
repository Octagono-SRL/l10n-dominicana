from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _compute_name(self):
        super()._compute_name()

        # l10n_latam skips the journal sequence for manually numbered documents
        # (vendor bills), but Dominican moves always keep their internal name.
        for move in self.filtered(
            lambda x: x.country_code == "DO"
            and x.l10n_latam_use_documents
            and x.l10n_latam_manual_document_number
            and x.state == "posted"
            and not x.name
        ):
            move._set_next_sequence()

        # Fiscal numbers (NCF/e-NCF) are assigned once the move is posted,
        # following its own sequence per document type.
        for move in self.filtered(
            lambda x: x.country_code == "DO"
            and x.l10n_latam_document_type_id
            and not x.l10n_latam_manual_document_number
            and not x.l10n_do_enable_first_sequence
            and x.state == "posted"
            and not x.l10n_do_fiscal_number
        ):
            move.with_context(is_l10n_do_seq=True)._set_next_sequence()
