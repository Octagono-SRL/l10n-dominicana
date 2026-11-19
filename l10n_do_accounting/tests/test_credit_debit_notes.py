from datetime import timedelta

from odoo import Command, fields
from odoo.tests import Form, tagged

from .common import L10nDOTestsCommon


@tagged("post_install", "-at_install")
class TestCreditDebitNotes(L10nDOTestsCommon):
    """Credit and debit notes: what l10n_do 14 / l10n_do_dgii_factura_electronica
    did, back on Odoo 17+ (reversal by copy(), no _reverse_move_vals nor
    _move_autocomplete_invoice_lines_create hooks)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.do_company.l10n_do_ecf_issuer = True
        company_id = cls.do_company.id
        cls.tax_18 = cls.env.ref("account.%s_tax_18_sale" % company_id)
        cls.tax_0_sale = cls.env.ref("account.%s_tax_0_sale" % company_id)
        cls.do_company.account_sale_tax_id = cls.tax_18
        cls.today = fields.Date.context_today(cls.env.user)

    # helpers

    def _number(self, move_or_wizard, seq):
        prefix = move_or_wizard.l10n_latam_document_type_id.doc_code_prefix
        return "%s%s" % (prefix, str(seq).zfill(10 if prefix.startswith("E") else 8))

    def _invoice(
        self,
        lines=None,
        move_type="out_invoice",
        number=1,
        partner=None,
        date=None,
        currency=None,
        doc=None,
    ):
        sale = move_type.startswith("out_")
        vals = {
            "move_type": move_type,
            "partner_id": (partner or self.fiscal_partner).id,
            "invoice_date": date or self.today,
            "journal_id": (
                self.fiscal_sale_journal if sale else self.fiscal_purchase_journal
            ).id,
            "invoice_line_ids": [
                Command.create(
                    {
                        "name": name,
                        "quantity": quantity,
                        "price_unit": price,
                        "tax_ids": [Command.set(taxes.ids)],
                    }
                )
                for name, quantity, price, taxes in (
                    lines or [("Servicio", 1, 1000.0, self.tax_18)]
                )
            ],
        }
        if not sale:
            vals["l10n_do_expense_type"] = "02"
        if currency:
            vals["currency_id"] = currency.id
        if doc:
            vals["l10n_latam_document_type_id"] = doc.id
        move = self.env["account.move"].create(vals)
        if number:
            move.l10n_latam_document_number = self._number(move, number)
        move.action_post()
        return move

    def _reversal(self, move, **vals):
        values = {
            "journal_id": move.journal_id.id,
            "reason": "Ajuste de precio",
            "date": move.invoice_date,
        }
        values.update(vals)
        return (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=move.ids)
            .create(values)
        )

    def _debit_wizard(self, move, **vals):
        values = {"reason": "Cargo adicional", "date": move.invoice_date}
        values.update(vals)
        wizard = (
            self.env["account.debit.note"]
            .with_context(active_model="account.move", active_ids=move.ids)
            .create(values)
        )
        wizard._onchange_move_id()  # the form view does it
        return wizard

    def _open(self, action):
        return self.env["account.move"].browse(action["res_id"])

    def _invoices_of(self, partner):
        return self.env["account.move"].search(
            [("partner_id", "=", partner.id), ("move_type", "=", "out_invoice")]
        )

    # A1: the reversal wizard opens (no Odoo 14 compute left)

    def test_wizard_manual_number(self):
        invoice = self._invoice()
        form = Form(
            self.env["account.move.reversal"].with_context(
                active_model="account.move", active_ids=invoice.ids
            )
        )
        self.assertFalse(form.l10n_latam_manual_document_number)

        bill = self._invoice(move_type="in_invoice", number=77)
        form = Form(
            self.env["account.move.reversal"].with_context(
                active_model="account.move", active_ids=bill.ids
            )
        )
        self.assertTrue(form.l10n_latam_manual_document_number)

    # A4: origin NCF and modification code on every credit note

    def test_wizard_fields_keep_their_labels(self):
        # A <group> nested in the field group (14 layout) made Odoo 17+ render
        # it as a group of columns: Motivo, Diario, Fecha, the e-CF code... all
        # without labels.
        from lxml import etree

        arch = self.env["account.move.reversal"].get_views([(False, "form")])["views"]["form"]["arch"]
        group = etree.fromstring(arch).xpath("//group[field[@name='reason']]")[0]
        self.assertFalse(group.xpath("./group"))
        self.assertTrue(group.xpath("./field[@name='l10n_do_ecf_modification_code']"))

    def test_full_credit_note_origin_and_code(self):
        invoice = self._invoice()
        refund = self._open(
            self._reversal(invoice, l10n_do_ecf_modification_code="1").refund_moves()
        )
        self.assertEqual(refund.move_type, "out_refund")
        self.assertEqual(refund.state, "draft")
        self.assertEqual(refund.l10n_do_origin_ncf, "E310000000001")
        self.assertEqual(refund.l10n_do_ecf_modification_code, "1")
        self.assertEqual(
            refund.l10n_latam_document_type_id, self.do_document_type["e-credit_note"]
        )
        self.assertEqual(refund.amount_total, invoice.amount_total)
        self.assertEqual(refund.invoice_line_ids.tax_ids, self.tax_18)

        refund.l10n_latam_document_number = "E340000000001"
        refund.action_post()
        self.assertEqual(refund.l10n_do_fiscal_number, "E340000000001")

    def test_vendor_credit_note_origin(self):
        bill = self._invoice(move_type="in_invoice", number=77)
        wizard = self._reversal(bill)
        wizard.l10n_latam_document_number = self._number(wizard, 12)
        refund = self._open(wizard.refund_moves())
        self.assertEqual(refund.move_type, "in_refund")
        self.assertEqual(refund.l10n_do_origin_ncf, bill.l10n_do_fiscal_number)
        self.assertTrue(refund.l10n_do_origin_ncf)

    # A4 + ITBIS: Percentage / Amount credit notes

    def test_percentage_credit_note_keeps_invoice_taxes(self):
        invoice = self._invoice(
            lines=[
                ("Servicio gravado", 2, 500.0, self.tax_18),
                ("Servicio exento", 1, 500.0, self.tax_0_sale),
            ]
        )
        refund = self._open(
            self._reversal(
                invoice,
                refund_type="percentage",
                percentage=10,
                l10n_do_ecf_modification_code="3",
            ).refund_moves()
        )
        self.assertEqual(refund.state, "draft")
        self.assertEqual(refund.l10n_do_origin_ncf, "E310000000001")
        self.assertEqual(refund.l10n_do_ecf_modification_code, "3")
        lines = refund.invoice_line_ids.sorted("price_unit")
        self.assertEqual(lines.mapped("name"), ["Ajuste de precio"] * 2)
        self.assertEqual(lines.mapped("quantity"), [1.0, 1.0])
        self.assertEqual(lines.mapped("price_unit"), [50.0, 100.0])
        self.assertEqual(lines[0].tax_ids, self.tax_0_sale)
        self.assertEqual(lines[1].tax_ids, self.tax_18)
        self.assertEqual(refund.amount_untaxed, 150.0)
        self.assertEqual(refund.amount_tax, 18.0)
        # the invoice is not touched and no copy of it appears
        self.assertEqual(invoice.payment_state, "not_paid")
        self.assertEqual(len(self._invoices_of(self.fiscal_partner)), 1)

    def test_amount_credit_note(self):
        invoice = self._invoice(
            lines=[
                ("Servicio gravado", 1, 1000.0, self.tax_18),
                ("Servicio exento", 1, 500.0, self.tax_0_sale),
            ]
        )
        refund = self._open(
            self._reversal(
                invoice, refund_type="fixed_amount", amount=300
            ).refund_moves()
        )
        self.assertEqual(refund.amount_untaxed, 300.0)
        self.assertEqual(
            sorted(refund.invoice_line_ids.mapped("price_unit")), [100.0, 200.0]
        )
        self.assertEqual(refund.amount_tax, 36.0)

    def test_amount_credit_note_no_cent_lost(self):
        invoice = self._invoice(
            lines=[
                ("A", 1, 100.0, self.tax_18),
                ("B", 1, 100.0, self.tax_0_sale),
                ("C", 1, 100.0, self.env["account.tax"]),
            ]
        )
        refund = self._open(
            self._reversal(invoice, refund_type="fixed_amount", amount=100).refund_moves()
        )
        self.assertEqual(len(refund.invoice_line_ids), 3)
        self.assertAlmostEqual(refund.amount_untaxed, 100.0)

    # A8: what each action / button does

    def test_partial_apply_refund_posts_and_reconciles(self):
        invoice = self._invoice()
        wizard = self._reversal(
            invoice,
            refund_type="percentage",
            percentage=10,
            refund_action="apply_refund",
            l10n_do_ecf_modification_code="3",
            l10n_latam_document_number="E340000000001",
        )
        refund = self._open(wizard.refund_moves())
        self.assertEqual(refund.state, "posted")
        self.assertEqual(refund.l10n_do_fiscal_number, "E340000000001")
        self.assertEqual(refund.payment_state, "paid")
        self.assertEqual(invoice.payment_state, "partial")
        self.assertAlmostEqual(invoice.amount_residual, 1180.0 - 118.0)
        # no stray draft copy of the invoice (the 17.0 port opened one)
        self.assertEqual(len(self._invoices_of(self.fiscal_partner)), 1)

    def test_partial_ignores_create_invoice_button(self):
        invoice = self._invoice()
        wizard = self._reversal(invoice, refund_type="percentage", percentage=10)
        refund = self._open(wizard.modify_moves())
        self.assertEqual(refund.move_type, "out_refund")
        self.assertEqual(refund.state, "draft")
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(len(self._invoices_of(self.fiscal_partner)), 1)

    def test_full_refund_and_create_invoice(self):
        invoice = self._invoice()
        wizard = self._reversal(
            invoice,
            l10n_do_ecf_modification_code="1",
            l10n_latam_document_number="E340000000001",
        )
        new_invoice = self._open(wizard.modify_moves())
        refund = self.env["account.move"].search(
            [("reversed_entry_id", "=", invoice.id)]
        )
        self.assertEqual(refund.state, "posted")
        self.assertEqual(refund.l10n_do_origin_ncf, "E310000000001")
        self.assertEqual(invoice.payment_state, "reversed")
        self.assertEqual(new_invoice.move_type, "out_invoice")
        self.assertEqual(new_invoice.state, "draft")

    def test_full_refund_button_keeps_draft(self):
        invoice = self._invoice()
        refund = self._open(self._reversal(invoice).refund_moves())
        self.assertEqual(refund.state, "draft")
        self.assertEqual(len(self._invoices_of(self.fiscal_partner)), 1)

    # A5: one E34 range for out_refund and in_refund

    def test_e34_sequence_shared(self):
        invoice = self._invoice()
        refund_1 = self._open(
            self._reversal(
                invoice, l10n_latam_document_number="E340000000001"
            ).refund_moves()
        )
        refund_1.action_post()
        self.assertEqual(refund_1.l10n_do_fiscal_number, "E340000000001")

        # a purchase we number ourselves (E41) gets its credit note from the
        # same E34 range
        purchase = self._invoice(
            lines=[("Compra", 1, 500.0, self.env["account.tax"])],
            move_type="in_invoice",
            partner=self.consumo_partner,
            doc=self.do_document_type["e-informal"],
        )
        self.assertFalse(purchase.l10n_latam_manual_document_number)
        in_refund = self._open(self._reversal(purchase).refund_moves())
        self.assertEqual(in_refund.move_type, "in_refund")
        self.assertEqual(
            in_refund.l10n_latam_document_type_id,
            self.do_document_type["e-credit_note"],
        )
        in_refund.action_post()
        self.assertEqual(in_refund.l10n_do_fiscal_number, "E340000000002")

        refund_3 = self._open(self._reversal(invoice).refund_moves())
        refund_3.action_post()
        self.assertEqual(refund_3.l10n_do_fiscal_number, "E340000000003")

    def test_b04_sequence_not_shared(self):
        refund = self.env["account.move"].new(
            {
                "move_type": "out_refund",
                "journal_id": self.fiscal_sale_journal.id,
                "partner_id": self.fiscal_partner.id,
                "l10n_latam_document_type_id": self.do_document_type["credit_note"].id,
            }
        )
        where, param = refund.with_context(
            is_l10n_do_seq=True
        )._get_last_sequence_domain()
        self.assertIn("move_type = %(move_type)s", where)
        self.assertEqual(param["move_type"], "out_refund")

    # A6: foreign currency credit note takes the invoice rate

    def test_foreign_currency_credit_note_rate(self):
        usd = self.env.ref("base.USD")
        usd.active = True
        day_1 = self.today - timedelta(days=60)
        day_2 = self.today - timedelta(days=10)
        self.env["res.currency.rate"].create(
            [
                {"name": day_1, "rate": 1 / 58.0, "currency_id": usd.id,
                 "company_id": self.do_company.id},
                {"name": day_2, "rate": 1 / 62.0, "currency_id": usd.id,
                 "company_id": self.do_company.id},
            ]
        )
        invoice = self._invoice(date=day_1, currency=usd)
        self.assertAlmostEqual(invoice.amount_total_signed, 1180.0 * 58.0)
        refund = self._open(
            self._reversal(invoice, date=day_2).refund_moves()
        )
        self.assertEqual(refund.invoice_date, day_2)
        self.assertAlmostEqual(refund.amount_total_signed, -1180.0 * 58.0)

        # a sale in the company currency is not affected
        self.assertEqual(
            self._invoice(number=False).currency_id, self.do_company.currency_id
        )

    # A2: debit note Percentage / Amount / Copy Lines

    def test_debit_note_percentage(self):
        invoice = self._invoice()
        wizard = self._debit_wizard(
            invoice,
            l10n_do_debit_type="percentage",
            l10n_do_percentage=10,
            l10n_do_ecf_modification_code="3",
        )
        debit = self._open(wizard.create_debit())
        self.assertEqual(debit.state, "draft")
        self.assertEqual(debit.debit_origin_id, invoice)
        self.assertEqual(debit.l10n_do_origin_ncf, "E310000000001")
        self.assertEqual(debit.l10n_do_ecf_modification_code, "3")
        self.assertEqual(
            debit.l10n_latam_document_type_id, self.do_document_type["e-debit_note"]
        )
        line = debit.invoice_line_ids
        self.assertEqual(len(line), 1)
        self.assertEqual(line.name, "Cargo adicional")
        self.assertEqual(line.quantity, 1)
        self.assertEqual(line.price_unit, 100.0)
        self.assertEqual(line.tax_ids, self.tax_18)
        self.assertEqual(line.account_id, wizard.l10n_do_account_id)

        debit.l10n_latam_document_number = "E330000000001"
        debit.action_post()
        self.assertEqual(debit.l10n_do_fiscal_number, "E330000000001")
        self.assertAlmostEqual(debit.amount_total, 118.0)

        # the next one is numbered automatically
        debit_2 = self._open(
            self._debit_wizard(
                invoice, l10n_do_debit_type="fixed_amount", l10n_do_amount=40
            ).create_debit()
        )
        self.assertEqual(debit_2.invoice_line_ids.price_unit, 40.0)
        debit_2.action_post()
        self.assertEqual(debit_2.l10n_do_fiscal_number, "E330000000002")

    def test_debit_note_after_30_days_is_exempt(self):
        invoice = self._invoice(date=self.today - timedelta(days=45))
        debit = self._open(
            self._debit_wizard(
                invoice,
                date=self.today,
                l10n_do_debit_type="percentage",
                l10n_do_percentage=10,
            ).create_debit()
        )
        self.assertEqual(debit.invoice_line_ids.tax_ids, self.tax_0_sale)

    def test_debit_note_apply_posts(self):
        invoice = self._invoice()
        debit = self._open(
            self._debit_wizard(
                invoice,
                l10n_do_debit_type="percentage",
                l10n_do_percentage=10,
                l10n_do_debit_action="apply_debit",
                l10n_latam_document_number="E330000000001",
            ).create_debit()
        )
        self.assertEqual(debit.state, "posted")
        self.assertEqual(debit.l10n_do_fiscal_number, "E330000000001")

    def test_debit_note_copy_lines(self):
        invoice = self._invoice(
            lines=[
                ("Producto A", 3, 200.0, self.tax_18),
                ("Producto B", 1, 50.0, self.tax_0_sale),
            ]
        )
        wizard = self._debit_wizard(invoice, copy_lines=True)
        debit = self._open(wizard.create_debit())
        self.assertEqual(
            debit.invoice_line_ids.mapped("name"), ["Producto A", "Producto B"]
        )
        self.assertEqual(debit.invoice_line_ids.mapped("quantity"), [3.0, 1.0])
        self.assertEqual(debit.invoice_line_ids[0].tax_ids, self.tax_18)
        self.assertEqual(debit.l10n_do_origin_ncf, "E310000000001")
        self.assertEqual(
            debit.l10n_latam_document_type_id, self.do_document_type["e-debit_note"]
        )

    def test_vendor_debit_note(self):
        bill = self._invoice(move_type="in_invoice", number=77)
        wizard = self._debit_wizard(
            bill, l10n_do_debit_type="fixed_amount", l10n_do_amount=200
        )
        wizard.l10n_latam_document_number = self._number(wizard, 5)
        debit = self._open(wizard.create_debit())
        self.assertEqual(debit.move_type, "in_invoice")
        self.assertEqual(debit.invoice_line_ids.price_unit, 200.0)
        self.assertEqual(
            debit.invoice_line_ids.tax_ids,
            self.do_company.account_purchase_tax_id,
        )
        self.assertEqual(debit.l10n_do_origin_ncf, bill.l10n_do_fiscal_number)

    # Vendor notes: an electronic supplier note (E34 / E33) asks for the
    # e-CF modification code in the wizard, as customer notes do, instead
    # of leaving it empty for the draft to ask again.

    def _doc(self, prefix):
        return self.env["l10n_latam.document.type"].search(
            [("doc_code_prefix", "=", prefix), ("country_id.code", "=", "DO")],
            limit=1,
        )

    def _vendor_ecf_bill(self):
        return self._invoice(move_type="in_invoice", number=77, doc=self._doc("E31"))

    def test_vendor_ecf_credit_note_asks_code(self):
        bill = self._vendor_ecf_bill()
        form = Form(
            self.env["account.move.reversal"].with_context(
                active_model="account.move", active_ids=bill.ids
            )
        )
        form.reason = "Devolucion"
        form.l10n_latam_document_type_id = self._doc("E34")
        form.l10n_latam_document_number = "E340000000012"
        form.l10n_do_ecf_modification_code = "3"
        refund = self._open(form.save().refund_moves())
        self.assertEqual(refund.move_type, "in_refund")
        self.assertEqual(refund.l10n_latam_document_type_id.doc_code_prefix, "E34")
        self.assertEqual(refund.l10n_do_ecf_modification_code, "3")
        self.assertEqual(refund.l10n_do_origin_ncf, bill.l10n_do_fiscal_number)

    def _reversal_form(self, move):
        return Form(
            self.env["account.move.reversal"].with_context(
                active_model="account.move", active_ids=move.ids
            )
        )

    def _debit_form(self, move):
        return Form(
            self.env["account.debit.note"].with_context(
                active_model="account.move", active_ids=move.ids
            )
        )

    def test_note_type_follows_the_invoice(self):
        # Marlan 2026-11-19: a credit note of an electronic invoice came as
        # B04 (first type of the list) and the code only showed after
        # choosing E34 by hand.
        ecf_bill = self._vendor_ecf_bill()
        form = self._reversal_form(ecf_bill)
        self.assertEqual(form.l10n_latam_document_type_id, self._doc("E34"))
        self.assertFalse(form._get_modifier("l10n_do_ecf_modification_code", "invisible"))
        self.assertTrue(form._get_modifier("l10n_do_ecf_modification_code", "required"))
        self.assertEqual(form.l10n_do_ecf_modification_code, "1")
        self.assertEqual(self._debit_form(ecf_bill).l10n_latam_document_type_id, self._doc("E33"))

        paper_bill = self._invoice(move_type="in_invoice", number=78, doc=self._doc("B01"))
        form = self._reversal_form(paper_bill)
        self.assertEqual(form.l10n_latam_document_type_id, self._doc("B04"))
        # always shown, required only for an electronic note
        self.assertFalse(form._get_modifier("l10n_do_ecf_modification_code", "invisible"))
        self.assertFalse(form._get_modifier("l10n_do_ecf_modification_code", "required"))
        form.l10n_latam_document_type_id = self._doc("E34")
        self.assertTrue(form._get_modifier("l10n_do_ecf_modification_code", "required"))
        self.assertEqual(self._debit_form(paper_bill).l10n_latam_document_type_id, self._doc("B03"))

    def test_modification_code_prefilled(self):
        """The code comes filled, the user only changes it if needed:
        01 for a full refund, 03 for Percentage/Amount and debit notes."""
        bill = self._vendor_ecf_bill()
        form = Form(
            self.env["account.move.reversal"].with_context(
                active_model="account.move", active_ids=bill.ids
            )
        )
        form.reason = "Devolucion"
        form.l10n_latam_document_type_id = self._doc("E34")
        self.assertEqual(form.l10n_do_ecf_modification_code, "1")
        form.refund_type = "percentage"
        self.assertEqual(form.l10n_do_ecf_modification_code, "3")
        form.refund_type = "full_refund"
        self.assertEqual(form.l10n_do_ecf_modification_code, "1")
        form.l10n_latam_document_number = "E340000000013"
        refund = self._open(form.save().refund_moves())
        self.assertEqual(refund.l10n_do_ecf_modification_code, "1")

        form = Form(
            self.env["account.debit.note"].with_context(
                active_model="account.move", active_ids=bill.ids
            )
        )
        form.reason = "Cargo adicional"
        form.l10n_latam_document_type_id = self._doc("E33")
        form.l10n_latam_document_number = "E330000000006"
        form.l10n_do_percentage = 10
        self.assertEqual(form.l10n_do_ecf_modification_code, "3")
        debit = self._open(form.save().create_debit())
        self.assertEqual(debit.l10n_do_ecf_modification_code, "3")

        invoice = self._invoice()
        refund = self._open(self._reversal(invoice).refund_moves())
        self.assertEqual(refund.l10n_do_ecf_modification_code, "1")
        refund = self._open(
            self._reversal(invoice, refund_type="fixed_amount", amount=100).refund_moves()
        )
        self.assertEqual(refund.l10n_do_ecf_modification_code, "3")
        debit = self._open(self._debit_wizard(invoice, l10n_do_percentage=5).create_debit())
        self.assertEqual(debit.l10n_do_ecf_modification_code, "3")
        wizard = self._debit_wizard(
            invoice, l10n_do_percentage=5, l10n_do_ecf_modification_code="2"
        )
        self.assertEqual(wizard.l10n_do_ecf_modification_code, "2")

    def test_vendor_ecf_debit_note_asks_code(self):
        bill = self._vendor_ecf_bill()
        form = Form(
            self.env["account.debit.note"].with_context(
                active_model="account.move", active_ids=bill.ids
            )
        )
        form.reason = "Cargo adicional"
        form.l10n_latam_document_type_id = self._doc("E33")
        form.l10n_latam_document_number = "E330000000005"
        form.l10n_do_percentage = 10
        form.l10n_do_ecf_modification_code = "3"
        debit = self._open(form.save().create_debit())
        self.assertEqual(debit.move_type, "in_invoice")
        self.assertEqual(debit.l10n_latam_document_type_id.doc_code_prefix, "E33")
        self.assertEqual(debit.l10n_do_ecf_modification_code, "3")
        self.assertEqual(debit.l10n_do_origin_ncf, bill.l10n_do_fiscal_number)

    # Printed e-CF: the QR (Odoo 15+ calls the /report/barcode parameter
    # "barcode_type"; with Odoo 14's "type" the image answered HTTP 500)

    def test_printed_ecf_has_qr(self):
        invoice = self._invoice()
        invoice.is_ecf_invoice = True  # stored compute, only refreshed on drafts
        invoice.write(
            {
                "l10n_do_ecf_security_code": "ABC123",
                "l10n_do_ecf_sign_date": fields.Datetime.now(),
            }
        )
        self.assertTrue(invoice.l10n_do_electronic_stamp)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "account.report_invoice", invoice.ids
        )[0].decode()
        self.assertIn("/report/barcode/?barcode_type=QR&amp;", html)
        self.assertNotIn("/report/barcode/?type=", html)
        self.assertTrue(
            self.env["ir.actions.report"].barcode(
                "QR", "https://ecf.dgii.gov.do/testecf/consultatimbre?rncemisor=1"
            )
        )

    # A3: Debit Note button next to Credit Note

    def test_debit_note_button(self):
        arch = self.env["account.move"].get_views([(False, "form")])["views"]["form"][
            "arch"
        ]
        self.assertIn('name="action_debit_note"', arch)
