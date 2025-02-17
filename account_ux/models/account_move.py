# flake8: noqa
import json
from odoo.tools import float_is_zero
from odoo import models, api, fields, _
from odoo.exceptions import UserError, ValidationError


class AccountMove(models.Model):
    _inherit = "account.move"

    internal_notes = fields.Text(
        'Internal Notes'
    )
    reversed_entry_id = fields.Many2one(
        'account.move',
        states={'draft': [('readonly', False)]},
    )

    def delete_number(self):
        self.filtered(lambda x: x.state == 'cancel').write({'name': '/'})

    def post(self):
        move_lines = self.mapped('line_ids').filtered(
            lambda x: (
                x.account_id.user_type_id.analytic_tag_required and
                x.account_id.analytic_tag_required != 'optional' or
                x.account_id.analytic_tag_required == 'required')
            and not x.analytic_tag_ids)
        if move_lines:
            raise UserError(_(
                "Some move lines don't have analytic tags and "
                "analytic tags are required by theese accounts.\n"
                "* Accounts: %s\n"
                "* Move lines ids: %s" % (
                    ", ".join(move_lines.mapped('account_id.name')),
                    move_lines.ids
                )
            ))

        move_lines = self.mapped('line_ids').filtered(
            lambda x: (
                x.account_id.user_type_id.analytic_account_required and
                x.account_id.analytic_account_required != 'optional' or
                x.account_id.analytic_account_required == 'required')
            and not x.analytic_account_id)
        if move_lines:
            raise UserError(_(
                "Some move lines don't have analytic account and "
                "analytic account is required by theese accounts.\n"
                "* Accounts: %s\n"
                "* Move lines ids: %s" % (
                    ", ".join(move_lines.mapped('account_id.name')),
                    move_lines.ids
                )
            ))
        res = super(AccountMove, self).post()
        return res

    def action_post(self):
        """ After validate invoice will sent an email to the partner if the related journal has mail_template_id set """
        res = super().action_post()
        self.action_send_invoice_mail()
        return res

    def action_send_invoice_mail(self):
        for rec in self.filtered(lambda x: x.is_invoice(include_receipts=True) and x.journal_id.mail_template_id):
            try:
                rec.message_post_with_template(
                    rec.journal_id.mail_template_id.id,
                )
            except Exception as error:
                title = _(
                    "ERROR: Invoice was not sent via email"
                )
                # message = _(
                #     "Invoice %s was correctly validate but was not send"
                #     " via email. Please review invoice chatter for more"
                #     " information" % rec.display_name
                # )
                # self.env.user.notify_warning(
                #     title=title,
                #     message=message,
                #     sticky=True,
                # )
                rec.message_post(body="<br/><br/>".join([
                    "<b>" + title + "</b>",
                    _("Please check the email template associated with"
                      " the invoice journal."),
                    "<code>" + str(error) + "</code>"
                ]),
                )

    @api.onchange('partner_id')
    def _onchange_partner_commercial(self):
        if self.partner_id.user_id:
            self.invoice_user_id = self.partner_id.user_id.id

    def copy(self, default=None):
        res = super().copy(default=default)
        res._onchange_partner_commercial()
        return res

    def _compute_payments_widget_to_reconcile_info(self):
        for move in self:
            move.invoice_outstanding_credits_debits_widget = json.dumps(False)
            move.invoice_has_outstanding = False

            if move.state != 'posted' \
                    or move.payment_state not in ('not_paid', 'partial') \
                    or not move.is_invoice(include_receipts=True):
                continue

            pay_term_lines = move.line_ids\
                .filtered(lambda line: line.account_id.user_type_id.type in ('receivable', 'payable'))

            domain = [
                ('account_id', 'in', pay_term_lines.account_id.ids),
                ('move_id.state', '=', 'posted'),
                ('partner_id', '=', move.commercial_partner_id.id),
                ('reconciled', '=', False),
                '|', ('amount_residual', '!=', 0.0), ('amount_residual_currency', '!=', 0.0),
            ]

            payments_widget_vals = {'outstanding': True, 'content': [], 'move_id': move.id}

            if move.is_inbound():
                domain.append(('balance', '<', 0.0))
                payments_widget_vals['title'] = _('Outstanding credits')
            else:
                domain.append(('balance', '>', 0.0))
                payments_widget_vals['title'] = _('Outstanding debits')

            for line in self.env['account.move.line'].search(domain):

                if line.currency_id == move.currency_id:
                    # Same foreign currency.
                    amount = abs(line.amount_residual_currency)
                else:
                    # Different foreign currencies.
                    if move.company_id.country_id == self.env.ref('base.ar'):
                        amount = move.company_currency_id._convert(
                            abs(line.amount_residual),
                            move.currency_id,
                            move.company_id,
                            move.invoice_date,
                        )
                    else:
                        amount = move.company_currency_id._convert(
                            abs(line.amount_residual),
                            move.currency_id,
                            move.company_id,
                            line.date,
                        )

                if move.currency_id.is_zero(amount):
                    continue

                payments_widget_vals['content'].append({
                    'journal_name': line.ref or line.move_id.name,
                    'amount': amount,
                    'currency': move.currency_id.symbol,
                    'id': line.id,
                    'move_id': line.move_id.id,
                    'position': move.currency_id.position,
                    'digits': [69, move.currency_id.decimal_places],
                    'payment_date': fields.Date.to_string(line.date),
                })

            if not payments_widget_vals['content']:
                continue

            move.invoice_outstanding_credits_debits_widget = json.dumps(payments_widget_vals)
            move.invoice_has_outstanding = True

    def _get_reconciled_info_JSON_values(self):
        self.ensure_one()
        foreign_currency = self.currency_id if self.currency_id != self.company_id.currency_id else False

        #To-do debit or credit currency 
        field_currency = 'debit' if True else 'credit'
        reconciled_vals = []
        pay_term_line_ids = self.line_ids.filtered(lambda line: line.account_id.user_type_id.type in ('receivable', 'payable'))
        partials = pay_term_line_ids.mapped('matched_debit_ids') + pay_term_line_ids.mapped('matched_credit_ids')
        for partial in partials:
            counterpart_lines = partial.debit_move_id + partial.credit_move_id
            # In case we are in an onchange, line_ids is a NewId, not an integer. By using line_ids.ids we get the correct integer value.
            counterpart_line = counterpart_lines.filtered(lambda line: line.id not in self.line_ids.ids)

            if foreign_currency and partial['%s_currency_id' % field_currency].id == foreign_currency:
                amount = partial['%s_amount_currency'  % field_currency]
            else:
                # For a correct visualization of the amounts, we use the currency rate from the invoice.
                amount = partial.company_currency_id._convert(partial.amount, self.currency_id, self.company_id, self.date)
                # INICIO CAMBIO
                if self.company_id.country_id == self.env.ref('base.ar'):
                    if self._fields.get('l10n_ar_currency_rate') and self.l10n_ar_currency_rate and self.l10n_ar_currency_rate != 1.0:
                        amount = self.currency_id.round(abs(partial.amount) / self.l10n_ar_currency_rate)
                # FIN CAMBIO
            if float_is_zero(amount, precision_rounding=self.currency_id.rounding):
                continue

            ref = counterpart_line.move_id.name
            if counterpart_line.move_id.ref:
                ref += ' (' + counterpart_line.move_id.ref + ')'

            reconciled_vals.append({
                'name': counterpart_line.name,
                'journal_name': counterpart_line.journal_id.name,
                'amount': amount,
                'currency': self.currency_id.symbol,
                'digits': [69, self.currency_id.decimal_places],
                'position': self.currency_id.position,
                'date': counterpart_line.date,
                'payment_id': counterpart_line.id,
                'account_payment_id': counterpart_line.payment_id.id,
                'payment_method_name': counterpart_line.payment_id.payment_method_id.name if counterpart_line.journal_id.type == 'bank' else None,
                'move_id': counterpart_line.move_id.id,
                'ref': ref,
            })
        return reconciled_vals

    @api.constrains('state', 'move_type', 'journal_id')
    def check_invoice_and_journal_type(self, default=None):
        """ Only let to create customer invoices/vendor bills in respective sale/purchase journals """
        error = self.filtered(
            lambda x: x.is_sale_document() and x.journal_id.type != 'sale' or
            x.is_purchase_document() and x.journal_id.type != 'purchase')
        if error:
            raise ValidationError(_(
                'You can create sales/purchase invoices exclusively in the respective sales/purchase journals'))

    def unlink(self):
        """ If we delete a journal entry that is related to a reconcile line then we need to clean the statement line
        in order to be able to reconcile in the future (clean up the move_name field)."""
        # self.mapped('line_ids.statement_line_id').write({'move_name': False})
        return super().unlink()
