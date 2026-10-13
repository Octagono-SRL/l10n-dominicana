from odoo import models, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def register_as_main_attachment(self, force=True):
        """
        Versión segura que:
        1. Verifica existencia del objeto
        2. Comprueba permisos
        3. Valida conciliación
        4. Maneja errores adecuadamente
        """
        try:
            # Solo aplica para facturas
            if self.res_model == 'account.move' and self.res_id:
                invoice = self.env['account.move'].browse(self.res_id)

                # Verificación completa de existencia y acceso
                if not invoice.exists():
                    _logger.error("Intento de adjuntar a factura inexistente: %s", self.res_id)
                    raise UserError(_("La factura especificada no existe"))

                if not invoice.check_access_rights('write', raise_exception=False):
                    _logger.warning(
                        "Intento no autorizado de modificar factura %s por el usuario %s",
                        invoice.name, self.env.user.name
                    )
                    raise UserError(_("No tiene permisos para modificar esta factura"))

                # Verificación de conciliación con fallback seguro
                if hasattr(invoice, '_is_reconciled'):
                    if invoice._is_reconciled():
                        _logger.info(
                            "Bloqueo de adjunto principal para factura conciliada %s",
                            invoice.name
                        )
                        # Retornar éxito pero sin modificar nada
                        return True
                else:
                    _logger.error("Método _is_reconciled() no disponible en account.move")

            # Flujo normal para no facturas o facturas no conciliadas
            return super().register_as_main_attachment(force=force)

        except UserError:
            raise  # Re-lanzamos errores de usuario
        except Exception as e:
            _logger.exception("Error crítico en registro de adjunto: %s", str(e))
            raise UserError(_(
                "Error técnico al registrar el archivo. "
                "Por favor contacte al administrador. Código: ATT01"
            )) from e