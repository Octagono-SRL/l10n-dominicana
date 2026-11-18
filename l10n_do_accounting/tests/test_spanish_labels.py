from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSpanishLabels(TransactionCase):
    """Users in es_ES (Birrosa, octagonosrl) saw "Modifies", "e-CF Modification Code",
    "Document Type"... in English: Odoo loads es.po + es_ES.po for them, never es_DO.po,
    and core ships the l10n_latam_invoice_document labels for es_419 only."""

    def _label(self, model, name, lang):
        return self.env["ir.model.fields"].with_context(lang=lang)._get(model, name).field_description

    def test_spanish_labels_for_every_spanish(self):
        modules = ["l10n_latam_invoice_document", "l10n_do_accounting"]
        for lang in ("es_ES", "es_DO"):
            self.env["res.lang"]._activate_lang(lang)
            self.env["ir.module.module"]._load_module_terms(modules, [lang])
            with self.subTest(lang=lang):
                self.assertEqual(self._label("account.move", "l10n_do_origin_ncf", lang), "Afecta a")
                self.assertEqual(
                    self._label("account.move", "l10n_do_ecf_modification_code", lang),
                    "Código Modificación e-CF",
                )
                self.assertEqual(self._label("account.move.reversal", "refund_type", lang), "Tipo de Reembolso")
                codes = dict(self.env["account.move"].with_context(lang=lang)._get_l10n_do_ecf_modification_code())
                self.assertEqual(codes["1"], "01 - Anulación total")
        # the latam labels: es_ES had none at all
        self.assertEqual(self._label("account.move", "l10n_latam_document_type_id", "es_ES"), "Tipo de Comprobante")
        self.assertEqual(self._label("account.move", "l10n_latam_document_number", "es_ES"), "Comprobante Fiscal")
