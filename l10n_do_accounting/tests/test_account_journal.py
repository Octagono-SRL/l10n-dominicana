from . import common
from odoo.tests import tagged
from odoo.exceptions import RedirectWarning


@tagged("post_install", "-at_install")
class AccountJournalTest(common.L10nDOTestsCommon):
    def test_001_raise_redirect(self):
        """
        Checks Journal raises RedirectWarning if trying to
        setup fiscal journal without company vat
        """

        journal = self.env["account.journal"].search(
            [
                ("type", "=", "sale"),
                ("company_id", "=", self.do_company.id),
            ],
            limit=1,
        )

        with self.assertRaises(RedirectWarning):
            self.do_company.vat = False
            journal._get_journal_ncf_types()

    def test_002_journal_without_company_vat(self):
        """A company with no RNC yet (new database) can create fiscal
        journals; their document types are added when the RNC is set."""
        company = self.env["res.company"].create(
            {"name": "DO company without RNC", "country_id": self.env.ref("base.do").id}
        )
        self.assertFalse(company.vat)
        journal = self.env["account.journal"].create(
            {
                "name": "Ventas sin RNC",
                "code": "VSR",
                "type": "sale",
                "company_id": company.id,
                "l10n_latam_use_documents": True,
            }
        )
        self.assertFalse(journal.l10n_do_document_type_ids)
        company.vat = "131793916"
        self.assertTrue(journal.l10n_do_document_type_ids)
