# © 2016 ADHOC SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, fields
import datetime
import logging
_logger = logging.getLogger(__name__)


class AccountPayment(models.Model):
    _inherit = "account.payment"

    def _get_liquidity_move_line_vals(self, amount):
        vals = super()._get_liquidity_move_line_vals(amount)
        days_for_collection = False
        journal = self.journal_id
        if (self.payment_method_code == 'inbound_debit_card'):
            days_for_collection = journal.debit_card_days_for_collection
        elif (self.payment_method_code == 'inbound_credit_card'):
            days_for_collection = journal.credit_card_days_for_collection
        if days_for_collection:
            vals['date_maturity'] = fields.Date.to_string(
                fields.Date.from_string(
                    self.payment_date) + datetime.timedelta(days=10))
        return vals

    def action_draft(self):
        """
        On payment back to draft delete move_name as we wont to allow deletion of
        payments. TODO: this could be parametrizable
        """
        res = super().action_draft()
        return res
    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    def _seek_for_lines_liquidity_accounts(self):

        self.ensure_one()
        accounts = [
                    self.journal_id.default_account_id,
                    self.journal_id.payment_debit_account_id,
                    self.journal_id.payment_credit_account_id,
        ] 
        return accounts

    def _seek_for_lines_counterpart_accounts(self, line):
        return line.account_id.internal_type in ('receivable', 'payable') or line.partner_id == line.company_id.partner_id

    def _seek_for_lines(self):
        ''' Helper used to dispatch the journal items between:
        - The lines using the temporary liquidity account.
        - The lines using the counterpart account.
        - The lines being the write-off lines.
        :return: (liquidity_lines, counterpart_lines, writeoff_lines)
        '''
        self.ensure_one()

        liquidity_lines = self.env['account.move.line']
        counterpart_lines = self.env['account.move.line']
        writeoff_lines = self.env['account.move.line']
        for line in self.move_id.line_ids:
            if line.account_id in self._seek_for_lines_liquidity_accounts():
                liquidity_lines += line
            elif self._seek_for_lines_counterpart_accounts(line):
                counterpart_lines += line
            else:
                writeoff_lines += line
        return liquidity_lines, counterpart_lines, writeoff_lines
