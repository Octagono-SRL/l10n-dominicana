from odoo import http, _
from odoo.http import request, content_disposition
import logging
from werkzeug.exceptions import Forbidden, NotFound

_logger = logging.getLogger(__name__)


class AccountMovePDFController(http.Controller):

    @http.route('/web/pdf_preview/safe', type='http', auth="user")
    def secure_pdf_preview(self, model, id, field, download=None, **kw):
        """
        Endpoint seguro que:
        1. Valida parámetros
        2. Verifica permisos
        3. Maneja facturas conciliadas
        4. Proporciona respuestas adecuadas
        """
        try:
            # Validación básica de parámetros
            if model != 'account.move' or not id or not id.isdigit():
                _logger.warning("Parámetros inválidos: model=%s, id=%s", model, id)
                raise Forbidden(_("Parámetros inválidos"))

            invoice = request.env['account.move'].browse(int(id))

            # Verificación de existencia
            if not invoice.exists():
                _logger.warning("Intento de acceder a factura inexistente: %s", id)
                raise NotFound(_("La factura no existe"))

            # Verificación de permisos
            if not invoice.check_access_rights('read', raise_exception=False):
                _logger.warning(
                    "Acceso no autorizado a factura %s por el usuario %s",
                    invoice.name, request.env.user.name
                )
                raise Forbidden(_("No tiene permisos para ver este documento"))

            # Manejo especial para facturas conciliadas
            is_reconciled = hasattr(invoice, '_is_reconciled') and invoice._is_reconciled()

            if is_reconciled:
                _logger.info(
                    "Visualización segura para factura conciliada %s",
                    invoice.name
                )

                # Generar PDF sin marcar como adjunto principal
                pdf_content = invoice.with_context(
                    no_register_attachment=True,
                    from_pdf_preview=True
                ).env['ir.actions.report']._run_wkhtmltopdf(
                    [invoice.id],
                    invoice.get_report_filename(),
                    {}
                )[0]

                headers = [
                    ('Content-Type', 'application/pdf'),
                    ('Content-Length', len(pdf_content)),
                ]

                if download:
                    headers.append(
                        ('Content-Disposition', content_disposition(f'{invoice.name}.pdf'))
                    )

                return request.make_response(pdf_content, headers=headers)

            # Flujo normal para facturas no conciliadas
            return request.env['ir.actions.report']._run_wkhtmltopdf(
                [int(id)],
                invoice.get_report_filename(),
                {'force_download': bool(download)}
            )

        except (Forbidden, NotFound) as e:
            raise  # Re-lanzamos excepciones HTTP conocidas
        except Exception as e:
            _logger.exception("Error generando PDF seguro")
            raise Forbidden(_("Error al generar el documento")) from e