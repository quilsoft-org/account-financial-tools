# © 2018 ADHOC SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import api, models, _
from odoo.exceptions import ValidationError


class ResCurrencyRate(models.Model):

    _inherit = 'res.currency.rate'

    @api.constrains('company_id')
    def _check_date_rate(self):
        for rec in self.filtered(lambda x: not x.company_id):
            others_with_company = self.search([
                ('name', '<=', rec.name),
                ('currency_id', '=', rec.currency_id.id),
                ('company_id', '!=', False),
            ])
            if others_with_company:
                raise ValidationError(_(
                    'You can not create a rate without company'
                    ' since you already have rates before %s with'
                    ' company set. The rate you want to create will not'
                    ' have any effect, will not be take into account.'
                ) % rec.name)

    def write(self, vals):

        res = super(ResCurrencyRate, self).write(vals)
        if vals.get('inverse_rate') or vals.get('rate'):
            invoces_in_draf = self.env['account.move'].search([('state','=','draft'),('currency_id','=',self.currency_id.id)])
            if invoces_in_draf:
                for invoice in invoces_in_draf:
                    new_invoice_lines = self.get_map_lines(invoice.invoice_line_ids.read())
                    invoice.invoice_line_ids = [(5,)]
                    invoice.write({'invoice_line_ids':new_invoice_lines})
                    invoice._recompute_dynamic_lines(recompute_all_taxes=True, recompute_tax_base_amount=True)

        return super(ResCurrencyRate, self).write(vals)

