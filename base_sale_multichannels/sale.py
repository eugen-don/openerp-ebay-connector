# -*- encoding: utf-8 -*-
#########################################################################
#                                                                       #
# Copyright (C) 2009  Raphaël Valyi                                     #
# Copyright (C) 2010-2011 Akretion Sébastien BEAU                       #
#                                        <sebastien.beau@akretion.com>  #
# Copyright (C) 2011-2012 Camptocamp Guewen Baconnier                   #
# Copyright (C) 2011 by Openlabs Technologies & Consulting (P) Limited  #
#                                                                       #
#This program is free software: you can redistribute it and/or modify   #
#it under the terms of the GNU General Public License as published by   #
#the Free Software Foundation, either version 3 of the License, or      #
#(at your option) any later version.                                    #
#                                                                       #
#This program is distributed in the hope that it will be useful,        #
#but WITHOUT ANY WARRANTY; without even the implied warranty of         #
#MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the          #
#GNU General Public License for more details.                           #
#                                                                       #
#You should have received a copy of the GNU General Public License      #
#along with this program.  If not, see <http://www.gnu.org/licenses/>.  #
#########################################################################

from osv import osv, fields
import pooler
from sets import Set as set
import netsvc
from tools.translate import _
import time
import decimal_precision as dp
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from tools import DEFAULT_SERVER_DATETIME_FORMAT, DEFAULT_SERVER_DATE_FORMAT

import logging

class StockPicking(osv.osv):
    '''Add a flag for marking picking as exported'''
    _inherit = 'stock.picking'

    _columns = {
        'exported_to_magento': fields.boolean('Exported to Magento',
            readonly=True),
    }

StockPicking()

class external_shop_group(osv.osv):
    _name = 'external.shop.group'
    _description = 'External Referential Shop Group'
    
    _columns = {
        'name': fields.char('Name', size=64, required=True),
        'referential_id': fields.many2one('external.referential', 'Referential', select=True, ondelete='cascade'),
        'shop_ids': fields.one2many('sale.shop', 'shop_group_id', 'Sale Shops'),
    }
    
external_shop_group()


class external_referential(osv.osv):
    _inherit = 'external.referential'
    
    _columns = {
        'shop_group_ids': fields.one2many('external.shop.group', 'referential_id', 'Sub Entities'),
    }

external_referential()


class product_category(osv.osv):
    _inherit = "product.category"
    
    def collect_children(self, category, children=None):
        if children is None:
            children = []

        for child in category.child_id:
            children.append(child.id)
            self.collect_children(child, children)

        return children
    
    def _get_recursive_children_ids(self, cr, uid, ids, name, args, context=None):
        res = {}
        for category in self.browse(cr, uid, ids):
            res[category.id] = self.collect_children(category, [category.id])
        return res

    _columns = {
        'recursive_childen_ids': fields.function(_get_recursive_children_ids, method=True, type='one2many', relation="product.category", string='All Child Categories'),
    }
    
product_category()

class sale_shop(osv.osv):
    _inherit = "sale.shop"

    _logger = logging.getLogger(__name__)
    def _get_exportable_product_ids(self, cr, uid, ids, name, args, context=None):
        res = {}
        for shop in self.browse(cr, uid, ids, context=context):
            root_categories = [category for category in shop.exportable_root_category_ids]
            all_categories = []
            for category in root_categories:
                all_categories += [category.id for category in category.recursive_childen_ids]

            # If product_m2mcategories module is installed search in main category and extra categories. If not, only in main category
            cr.execute('select * from ir_module_module where name=%s and state=%s', ('product_m2mcategories','installed'))
            if cr.fetchone():
                product_ids = self.pool.get("product.product").search(cr, uid, ['|',('categ_id', 'in', all_categories),('categ_ids', 'in', all_categories)])
            else:
                product_ids = self.pool.get("product.product").search(cr, uid, [('categ_id', 'in', all_categories)])
            res[shop.id] = product_ids
        return res

    def _get_referential_id(self, cr, uid, ids, name, args, context=None):
        res = {}
        for shop in self.browse(cr, uid, ids, context=context):
            if shop.shop_group_id:
                res[shop.id] = shop.shop_group_id.referential_id.id
                #path to fix orm bug indeed even if function field are store, the value is not store in the database
                cr.execute('update sale_shop set referential_id = %s where id=%s', (shop.shop_group_id.referential_id.id, shop.id))
            else:
                #path to fix orm bug indeed even if function field are store, the value is never read for many2one fields
                cr.execute('select referential_id from sale_shop where id=%s', (shop.id,))
                result = cr.fetchone()
                res[shop.id] = result[0]
        return res

    def _set_referential_id(self, cr, uid, id, name, value, arg, context=None):
        shop = self.browse(cr, uid, id, context=context)
        if shop.shop_group_id:
            raise osv.except_osv(_("User Error"), _("You can not change the referential of this shop, please change the referential of the shop group!"))
        else:
            if value == False:
                cr.execute('update sale_shop set referential_id = NULL where id=%s', (id,))
            else:
                cr.execute('update sale_shop set referential_id = %s where id=%s', (value, id))
        return True

    def _get_shop_ids(self, cr, uid, ids, context=None):
        shop_ids=[]
        for group in self.pool.get('external.shop.group').browse(cr, uid, ids, context=context):
            shop_ids += [shop.id for shop in group.shop_ids]
        return shop_ids

    def _get_stock_field_id(self, cr, uid, context=None):
        # TODO : Hidden dependency, put in a glue module ?
        if self.pool.get('ir.module.module').is_installed(
            cr, uid, 'stock_available_immediately', context=None):
            stock_field = 'immediately_usable_qty'
        else:
            stock_field = 'virtual_available'

        field_ids = self.pool.get('ir.model.fields').search(
            cr, uid,
            [('model', '=', 'product.product'),
             ('name', '=', stock_field)],
            context=context)
        return field_ids[0]

    _columns = {
        #'exportable_category_ids': fields.function(_get_exportable_category_ids, method=True, type='one2many', relation="product.category", string='Exportable Categories'),
        'exportable_root_category_ids': fields.many2many('product.category', 'shop_category_rel', 'categ_id', 'shop_id', 'Exportable Root Categories'),
        'exportable_product_ids': fields.function(_get_exportable_product_ids, method=True, type='one2many', relation="product.product", string='Exportable Products'),
        'shop_group_id': fields.many2one('external.shop.group', 'Shop Group', ondelete='cascade'),
        'last_inventory_export_date': fields.datetime('Last Inventory Export Time'),
        'last_images_export_date': fields.datetime('Last Images Export Time'),
        'last_update_order_export_date' : fields.datetime('Last Order Update  Time'),
        'last_products_export_date' : fields.datetime('Last Product Export  Time'),
        'referential_id': fields.function(_get_referential_id, fnct_inv = _set_referential_id, type='many2one',
                relation='external.referential', string='External Referential', method=True,
                store={
                    'sale.shop': (lambda self, cr, uid, ids, c=None: ids, ['shop_group_id'], 10),
                    'external.shop.group': (_get_shop_ids, ['referential_id'], 10),
                 }),
        'is_tax_included': fields.boolean('Prices Include Tax?', help="Requires sale_tax_include module to be installed"),
        'sale_journal': fields.many2one('account.journal', 'Sale Journal'),
        'order_prefix': fields.char('Order Prefix', size=64),
        'default_payment_method': fields.char('Default Payment Method', size=64),
        'default_language': fields.many2one('res.lang', 'Default Language'),
        'default_fiscal_position': fields.many2one('account.fiscal.position', 'Default Fiscal Position'),
        'default_customer_account': fields.many2one('account.account', 'Default Customer Account'),
        'auto_import': fields.boolean('Automatic Import'),
        'address_id':fields.many2one('res.partner.address', 'Address'),
        'website': fields.char('Website', size=64),
        'image':fields.binary('Image', filters='*.png,*.jpg,*.gif'),
        'import_orders_from_date': fields.datetime('Only created after'),
        'product_stock_field_id': fields.many2one(
            'ir.model.fields',
            string='Stock Field',
            domain="[('model', 'in', ['product.product', 'product.template']),"
                   " ('ttype', '=', 'float')]",
            help="Choose the field of the product which will be used for "
                 "stock inventory updates.\nIf empty, Quantity Available "
                 "is used")
    }
    
    _defaults = {
        'payment_default_id': lambda * a: 1, #required field that would cause trouble if not set when importing
        'auto_import': lambda * a: True,
        'product_stock_field_id': _get_stock_field_id,
    }

    def _get_pricelist(self, cr, uid, shop):
        if shop.pricelist_id:
            return shop.pricelist_id.id
        else:
            return self.pool.get('product.pricelist').search(cr, uid, [('type', '=', 'sale'), ('active', '=', True)])[0]
    
    def export_categories(self, cr, uid, shop, context=None):
        if context is None:
            context = {}
        categories = set([])
        categ_ids = []
        for category in shop.exportable_root_category_ids:
            categ_ids = self.pool.get('product.category')._get_recursive_children_ids(cr, uid, [category.id], "", [], context)[category.id]
            for categ_id in categ_ids:
                categories.add(categ_id)
        context['shop_id'] = shop.id
        self.pool.get('product.category').ext_export(cr, uid, [categ_id for categ_id in categories], [shop.referential_id.id], {}, context)
       
    def export_products_collection(self, cr, uid, shop, context):
        product_to_export = context.get('force_product_ids', [product.id for product in shop.exportable_product_ids])
        self.pool.get('product.product').ext_export(cr, uid, product_to_export, [shop.referential_id.id], {}, context)

    def export_products(self, cr, uid, shop, context):
        self.export_products_collection(cr, uid, shop, context)
    
    def export_catalog(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        report_obj = self.pool.get('external.report')
        for shop in self.browse(cr, uid, ids):
            context['shop_id'] = shop.id
            report_id = report_obj.start_report(cr, uid,
                                                ref='export_catalog',
                                                external_referential_id=shop.referential_id.id,
                                                context=context)
            context['external_report_id'] = report_id
            context['conn_obj'] = shop.referential_id.external_connection()
            self.export_categories(cr, uid, shop, context)
            self.export_products(cr, uid, shop, context)
            shop.write({'last_products_export_date' : time.strftime(DEFAULT_SERVER_DATETIME_FORMAT)})
            report_obj.end_report(cr, uid, report_id, context=context)
        self.export_inventory(cr, uid, ids, context)
        return False
            
    def export_inventory(self, cr, uid, ids, context=None):
        if context is None:
            context = {}

        stock_move_obj = self.pool.get('stock.move')
        for shop in self.browse(cr, uid, ids):
            connection = shop.referential_id.external_connection()

            product_ids = [product.id for product
                           in shop.exportable_product_ids]
            if shop.last_inventory_export_date:
                # we do not exclude canceled moves because it means
                # some stock levels could have increased since last export
                recent_move_ids = stock_move_obj.search(
                    cr, uid,
                    [('write_date', '>', shop.last_inventory_export_date),
                     ('product_id', 'in', product_ids),
                     ('state', '!=', 'draft')],
                    context=context)
            else:
                recent_move_ids = stock_move_obj.search(
                    cr, uid,
                    [('product_id', 'in', product_ids)],
                    context=context)

            recent_moves = stock_move_obj.browse(
                cr, uid, recent_move_ids, context=context)

            product_ids = [move.product_id.id
                           for move
                           in recent_moves
                           if move.product_id.state != 'obsolete']
            product_ids = list(set(product_ids))

            res = self.pool.get('product.product').export_inventory(
                cr, uid, product_ids, shop.id, connection, context=context)
            shop.write({'last_inventory_export_date':
                            time.strftime(DEFAULT_SERVER_DATETIME_FORMAT)})
        return res
    
    def import_catalog(self, cr, uid, ids, context):
        #TODO import categories, then products
        raise osv.except_osv(_("Not Implemented"), _("Not Implemented in abstract base module!"))
        
    def import_orders(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        for shop in self.browse(cr, uid, ids):
            defaults = {
                            'pricelist_id':self._get_pricelist(cr, uid, shop),
                            'shop_id': shop.id,
                            'fiscal_position': shop.default_fiscal_position.id,
                            'property_account_receivable': shop.default_customer_account.id,
                            'order_prefix': shop.order_prefix,
                            'ext_payment_method': shop.default_payment_method,
                            'order_policy':'prepaid',
                        }
            print ">>>>>>>>>>>>!@!", shop.referential_id.id, shop.referential_id.external_connection()
            context.update({
                            'conn_obj': shop.referential_id.external_connection(),
                            'shop_name': shop.name,
                            'shop_id': shop.id,
                            'external_referential_type': shop.referential_id.type_id.name,
                        })

            if self.pool.get('ir.model.fields').search(cr, uid, [('name', '=', 'company_id'), ('model', '=', 'sale.shop')]): #OpenERP v6 needs a company_id field on the sale order but v5 doesn't have it, same for shop...
                if not shop.company_id.id:
                    raise osv.except_osv(_('Warning!'), _('You have to set a company for this OpenERP sale shop!'))
                defaults.update({'company_id': shop.company_id.id})

            if shop.is_tax_included:
                defaults.update({'price_type': 'tax_included'})

            defaults.update(self.pool.get('sale.order').onchange_shop_id(cr, uid, ids, shop.id)['value'])

            self.import_shop_orders(cr, uid, shop, defaults, context)
        return False
            
    def import_shop_orders(self, cr, uid, shop, defaults, context):
        """Not Implemented in abstract base module!"""
        return defaults

    def update_orders(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        for shop in self.browse(cr, uid, ids):
            context['conn_obj'] = shop.referential_id.external_connection()
            #get all orders, which the state is not draft and the date of modification is superior to the last update, to exports 
            req = "select ir_model_data.res_id, ir_model_data.name from sale_order inner join ir_model_data on sale_order.id = ir_model_data.res_id where ir_model_data.model='sale.order' and sale_order.shop_id=%s and ir_model_data.external_referential_id NOTNULL and sale_order.state != 'draft'"
            param = (shop.id,)

            if shop.last_update_order_export_date:
                req += "and sale_order.write_date > %s"
                param = (shop.id, shop.last_update_order_export_date)

            cr.execute(req, param)
            results = cr.fetchall()

            for result in results:
                ids = self.pool.get('sale.order').search(cr, uid, [('id', '=', result[0])])
                if ids:
                    id = ids[0]
                    order = self.pool.get('sale.order').browse(cr, uid, id, context)
                    order_ext_id = result[1].split('sale_order/')[1]
                    self.update_shop_orders(cr, uid, order, order_ext_id, context)
                    self._logger.info("Successfully updated order with OpenERP id %s and ext id %s in external sale system" % (id, order_ext_id))
            self.pool.get('sale.shop').write(cr, uid, shop.id, {'last_update_order_export_date': time.strftime(DEFAULT_SERVER_DATETIME_FORMAT)})
        return False

    def update_shop_partners(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        context.update({'force': True}) #FIXME
        for shop in self.browse(cr, uid, ids):
            context['conn_obj'] = shop.referential_id.external_connection()
            ids = self.pool.get('res.partner').search(cr, uid, [('store_id', '=', self.pool.get('sale.shop').oeid_to_extid(cr, uid, shop.id, shop.referential_id.id, context))])
            self.pool.get('res.partner').ext_export(cr, uid, ids, [shop.referential_id.id], {}, context)
        return True
        
    def update_shop_orders(self, cr, uid, order, ext_id, context):
        raise osv.except_osv(_("Not Implemented"), _("Not Implemented in abstract base module!"))

    def export_shipping(self, cr, uid, ids, context):
        picking_obj = self.pool.get('stock.picking')
        for shop in self.browse(cr, uid, ids):
            cr.execute("""
                select stock_picking.id as picking_id, sale_order.id as order_id, count(pickings.id) as picking_number,
                       delivery_carrier.export_needs_tracking as need_tracking, stock_picking.carrier_tracking_ref as carrier_tracking
                from stock_picking
                left join sale_order on sale_order.id = stock_picking.sale_id
                left join stock_picking as pickings on sale_order.id = pickings.sale_id
                left join ir_model_data on stock_picking.id = ir_model_data.res_id and ir_model_data.model='stock.picking'
                left join delivery_carrier on delivery_carrier.id = stock_picking.carrier_id
                where shop_id = %s and ir_model_data.res_id ISNULL and stock_picking.state = 'done' and stock_picking.exported_to_magento IS NOT TRUE
                Group By stock_picking.id, sale_order.id,
                         delivery_carrier.export_needs_tracking, stock_picking.carrier_tracking_ref
                """, (shop.id,))
            results = cr.dictfetchall()
            if not results:
                self._logger.info("There is no shipping to export for the shop '%s' to the external referential" % (shop.name,))
                return True
            context['conn_obj'] = shop.referential_id.external_connection()


            picking_cr = pooler.get_db(cr.dbname).cursor()
            try:
                for result in results:
                    if result["picking_number"] == 1:
                        picking_type = 'complete'
                    else:
                        picking_type = 'partial'

                   # only export the shipping if a tracking number exists when the flag
                   # export_needs_tracking is flagged on the delivery carrier
                    if result["need_tracking"] and not result["carrier_tracking"]:
                        continue

                    # Mark shipping as exported here itself because export might
                    # fail in next step due to only one visible reason, i.e.,
                    # shipping already exists in magento which does not need to be
                    # exported anyway.
                    # FIXME: to refactorize
                    # Guewen Baconnier wrote:
                    #
                    #  1. this column should not be named "exported_to_magento" as base_sale_multichannels should be "external_referential-agnostic".
                    #     I think we could rename it as "do_not_export" because according to the above description, "it is NOT exported and we
                    #     do NOT want to export it". Also we could add it on the stock.picking.out view to reactivate the
                    #     export or prevent an export if it is done manually.
                    #
                    #  2. The issue with this implementation is that the exception management is way too large. With any sort of error,
                    #     the "exported_to_magento" field will be set to true and the picking will never be exported agin (python error, network error or anything).
                    #     What we should do : in create_ext_partial_shipping and create_ext_complete_shipping we do not silent the exception (it is except Exception: now!)
                    #     We catch it at this level with a fine exception management, only errors returned by magento with some error codes must write "do_not_export".
                    picking_obj.write(cr, uid, result["picking_id"], {
                            'exported_to_magento': True
                        }, context=context)

                    ext_shipping_id = self.pool.get('stock.picking').create_ext_shipping(picking_cr, uid, result["picking_id"], picking_type, shop.referential_id.id, context)

                    if ext_shipping_id:
                        ir_model_data_vals = {
                            'name': "stock_picking/" + str(ext_shipping_id),
                            'model': "stock.picking",
                            'res_id': result["picking_id"],
                            'external_referential_id': shop.referential_id.id,
                            'module': 'extref/' + shop.referential_id.name
                          }
                        self.pool.get('ir.model.data').create(picking_cr, uid, ir_model_data_vals)
                        self._logger.info("Successfully creating shipping with OpenERP id %s and ext id %s in external sale system" % (result["picking_id"], ext_shipping_id))
                    picking_cr.commit()
            finally:
                picking_cr.close()
        return True

sale_shop()


class sale_order(osv.osv):
    _inherit = "sale.order"

    _columns = {
                'ext_payment_method': fields.char('External Payment Method', size=32, help = "Spree, Magento, Oscommerce... Payment Method"),
                'need_to_update': fields.boolean('Need To Update'),
                'ext_total_amount': fields.float('Origin External Amount', digits_compute=dp.get_precision('Sale Price'), readonly=True),
    }

    _defaults = {
        'need_to_update': lambda *a: False,
    }

    _logger = logging.getLogger(__name__)
    def oevals_from_extdata(self, cr, uid, external_referential_id, data_record, key_field, mapping_lines, defaults, context):
        res = super(sale_order, self).oevals_from_extdata(cr, uid, external_referential_id, data_record, key_field, mapping_lines, defaults, context)
        payment_method = res.get('ext_payment_method', False) or defaults.get('ext_payment_method', False)
        payment_settings = self.payment_code_to_payment_settings(cr, uid, payment_method, context)
        if payment_settings:
            res['order_policy'] = payment_settings.order_policy
            res['picking_policy'] = payment_settings.picking_policy
            res['invoice_quantity'] = payment_settings.invoice_quantity
        return res

    def create_payments(self, cr, uid, order_id, data_record, context):
        """not implemented in this abstract module"""
        #TODO use the mapping tools from the data_record to extract the information about the payment
        return False

    def oe_status_and_paid(self, cr, uid, order_id, data, external_referential_id, defaults, context):
        is_paid = self.create_payments(cr, uid, order_id, data, context)
        self.oe_status(cr, uid, order_id, is_paid, context)
        return order_id

    def oe_create(self, cr, uid, vals, data, external_referential_id, defaults, context):
        order_id = super(sale_order, self).oe_create(cr, uid, vals, data, external_referential_id, defaults, context)
        self.oe_status_and_paid(cr, uid, order_id, data, external_referential_id, defaults, context)
        return order_id

    def generate_payment_from_order(self, cr, uid, ids, payment_ref, entry_name=None, paid=True, date=None, context=None):
        if type(ids) in [int, long]:
            ids = [ids]
        res = []
        for order in self.browse(cr, uid, ids, context=context):
            id = self.generate_payment_with_pay_code(cr, uid,
                                                    order.ext_payment_method,
                                                    order.partner_id.id,
                                                    order.ext_total_amount or order.amount_total,
                                                    payment_ref,
                                                    entry_name or order.name,
                                                    date or order.date_order,
                                                    paid,
                                                    context)
            id and res.append(id)
        return res

    def payment_code_to_payment_settings(self, cr, uid, payment_code, context=None):
        pay_type_obj = self.pool.get('base.sale.payment.type')
        payment_setting_ids = pay_type_obj.search(cr, uid, [['name', 'like', payment_code]])
        payment_setting_id = False
        for type in pay_type_obj.read(cr, uid, payment_setting_ids, fields=['name'], context=context):
            if payment_code in [x.strip() for x in type['name'].split(';')]:
                payment_setting_id = type['id']
        return payment_setting_id and pay_type_obj.browse(cr, uid, payment_setting_id, context) or False

    def generate_payment_with_pay_code(self, cr, uid, payment_code, partner_id, amount, payment_ref, entry_name, date, paid, context):
        payment_settings = self.payment_code_to_payment_settings(cr, uid, payment_code, context)
        if payment_settings and payment_settings.journal_id and (payment_settings.check_if_paid and paid or not payment_settings.check_if_paid):
            return self.generate_payment_with_journal(cr, uid, payment_settings.journal_id.id, partner_id, amount, payment_ref, entry_name, date, payment_settings.validate_payment, context)
        return False
        
    def generate_payment_with_journal(self, cr, uid, journal_id, partner_id, amount, payment_ref, entry_name, date, should_validate, context):
        voucher_obj = self.pool.get('account.voucher')
        voucher_line_obj = self.pool.get('account.voucher.line')
        data = voucher_obj.onchange_partner_id(cr, uid, [], partner_id, journal_id, int(amount), False, 'receipt', date, context)['value']
        account_id = data['account_id']
        statement_vals = {
                            'reference': 'ST_' + entry_name,
                            'journal_id': journal_id,
                            'amount': amount,
                            'date' : date,
                            'partner_id': partner_id,
                            'account_id': account_id,
                            'type': 'receipt',
                        }
        if data.get('payment_rate_currency_id'):
            statement_vals['payment_rate_currency_id'] = data['payment_rate_currency_id']
        statement_id = voucher_obj.create(cr, uid, statement_vals, context)
        context.update({'type': 'receipt', 'partner_id': partner_id, 'journal_id': journal_id, 'default_type': 'cr'})
        line_account_id = voucher_line_obj.default_get(cr, uid, ['account_id'], context)['account_id']
        statement_line_vals = {
                                'voucher_id': statement_id,
                                'amount': amount,
                                'account_id': line_account_id,
                                'type': 'cr',
                               }
        statement_line_id = voucher_line_obj.create(cr, uid, statement_line_vals, context)
        if should_validate:
            wf_service = netsvc.LocalService("workflow")
            wf_service.trg_validate(uid, 'account.voucher', statement_id, 'proforma_voucher', cr)
        return statement_id


    def oe_status(self, cr, uid, ids, paid = True, context = None):
        if type(ids) in [int, long]:
            ids =[ids]
        wf_service = netsvc.LocalService("workflow")
        for order in self.browse(cr, uid, ids, context):
            payment_settings = self.payment_code_to_payment_settings(cr, uid, order.ext_payment_method, context)
            
            if payment_settings:
                if payment_settings.payment_term_id:
                    self.write(cr, uid, order.id, {'payment_term': payment_settings.payment_term_id.id})

                if payment_settings.check_if_paid and not paid:
                    if order.state == 'draft' and datetime.strptime(order.date_order, DEFAULT_SERVER_DATE_FORMAT) < datetime.now() - relativedelta(days=payment_settings.days_before_order_cancel or 30):
                        wf_service.trg_validate(uid, 'sale.order', order.id, 'cancel', cr)
                        self.write(cr, uid, order.id, {'need_to_update': False})
                        self.log(cr, uid, order.id, "order %s canceled in OpenERP because older than % days and still not confirmed" % (order.id, payment_settings.days_before_order_cancel or 30))
                        #TODO eventually call a trigger to cancel the order in the external system too
                        self._logger.info("order %s canceled in OpenERP because older than % days and still not confirmed" % (order.id, payment_settings.days_before_order_cancel or 30))
                    else:
                        self.write(cr, uid, order.id, {'need_to_update': True})
                else:
                    if payment_settings.validate_order:
                        try:
                            wf_service.trg_validate(uid, 'sale.order', order.id, 'order_confirm', cr)
                            self.write(cr, uid, order.id, {'need_to_update': False})
                        except Exception:
                            self.log(cr, uid, order.id, "ERROR could not valid order")
                            raise

                        if payment_settings.validate_picking:
                            self.pool.get('stock.picking').validate_picking_from_order(cr, uid, order.id)
                        
                        cr.execute('select * from ir_module_module where name=%s and state=%s', ('mrp','installed'))
                        if payment_settings.validate_manufactoring_order and cr.fetchone(): #if mrp module is installed
                            self.pool.get('stock.picking').validate_manufactoring_order(cr, uid, order.name, context)

                        if order.order_policy == 'prepaid':
                            if payment_settings.validate_invoice:
                                for invoice in order.invoice_ids:
                                    wf_service.trg_validate(uid, 'account.invoice', invoice.id, 'invoice_open', cr)
                                    if payment_settings.is_auto_reconcile:
                                        invoice.auto_reconcile(context=context)
            
                        elif order.order_policy == 'manual':
                            if payment_settings.create_invoice:
                               wf_service.trg_validate(uid, 'sale.order', order.id, 'manual_invoice', cr)
                               invoice_id = self.browse(cr, uid, order.id).invoice_ids[0].id
                               if payment_settings.validate_invoice:
                                   wf_service.trg_validate(uid, 'account.invoice', invoice_id, 'invoice_open', cr)
                                   if payment_settings.is_auto_reconcile:
                                       self.pool.get('account.invoice').auto_reconcile(cr, uid, [invoice_id], context=context)
            
                        # IF postpaid DO NOTHING
            
                        elif order.order_policy == 'picking':
                            if payment_settings.create_invoice:
                                try:
                                    invoice_id = self.pool.get('stock.picking').action_invoice_create(cr, uid, [picking.id for picking in order.picking_ids])
                                except Exception, e:
                                    self.log(cr, uid, order.id, "Cannot create invoice from picking for order %s" %(order.name,))
                                if payment_settings.validate_invoice:
                                    wf_service.trg_validate(uid, 'account.invoice', invoice_id, 'invoice_open', cr)
                                    if payment_settings.is_auto_reconcile:
                                        self.pool.get('account.invoice').auto_reconcile(cr, uid, [invoice_id], context=context)

        return True

    def _prepare_invoice(self, cr, uid, order, lines, context=None):
        """Prepare the dict of values to create the new invoice for a
           sale order. This method may be overridden to implement custom
           invoice generation (making sure to call super() to establish
           a clean extension chain).

           :param browse_record order: sale.order record to invoice
           :param list(int) lines: list of invoice line IDs that must be
                                  attached to the invoice
           :return: dict of value to create() the invoice
        """
        vals = super(sale_order, self)._prepare_invoice(cr, uid, order, lines, context=context)
        if order.shop_id.sale_journal:
            vals['journal_id'] = order.shop_id.sale_journal.id
        return vals

    def action_invoice_create(self, cr, uid, ids, grouped=False, states=['confirmed', 'done', 'exception'], date_inv = False, context=None):
        inv_obj = self.pool.get('account.invoice')
        wf_service = netsvc.LocalService("workflow")
        res = super(sale_order, self).action_invoice_create(cr, uid, ids, grouped, states, date_inv, context)
        
        for order in self.browse(cr, uid, ids, context=context):
            payment_settings = self.payment_code_to_payment_settings(cr, uid, order.ext_payment_method, context=context)
            if payment_settings and payment_settings.invoice_date_is_order_date:
                inv_obj.write(cr, uid, [inv.id for inv in order.invoice_ids], {'date_invoice' : order.date_order}, context=context)
            if order.order_policy == 'postpaid':
                if payment_settings and payment_settings.validate_invoice:
                    for invoice in order.invoice_ids:
                        wf_service.trg_validate(uid, 'account.invoice', invoice.id, 'invoice_open', cr)
                        if payment_settings.is_auto_reconcile:
                            invoice.auto_reconcile(context=context)
        return res

    def oe_update(self, cr, uid, existing_rec_id, vals, each_row, external_referential_id, defaults, context):
        '''Not implemented in this abstract module, if it's not implemented in your module it will raise an error'''
        # Explication :
        # sometime customer can do ugly thing like renamming a sale_order and try to reimported it,
        # sometime openerp run two scheduler at the same time, or the customer launch two openerp at the same time
        # or the external system give us again an already imported order
        # As the update of an existing order (this is not the update of the status but the update of the line, the address...)
        # is not supported by base_sale_multichannels and also not in magentoerpconnect.
        # It's better to don't allow this feature to avoid hidding a problem.
        # It's better to have the order not imported and to know it than having order with duplicated line.
        if not (context and context.get('oe_update_supported', False)):
            #TODO found a clean solution to raise the osv.except_osv error in the try except of the function import_with_try
            raise osv.except_osv(_("Not Implemented"), _(("The order with the id %s try to be updated from the external system"
                                "This feature is not supported. Maybe the import try to reimport an existing sale order"%(existing_rec_id,))))
        return existing_rec_id

sale_order()

class sale_order_line(osv.osv):
    _inherit='sale.order.line'

    _columns = {
        'ext_product_ref': fields.char('Product Ext Ref', help="This is the original external product reference", size=256),
    }

sale_order_line()

class base_sale_payment_type(osv.osv):
    _name = "base.sale.payment.type"
    _description = "Base Sale Payment Type"

    _columns = {
        'name': fields.char('Payment Codes', help="List of Payment Codes separated by ;", size=256, required=True),
        'journal_id': fields.many2one('account.journal','Payment Journal'),
        'picking_policy': fields.selection([('direct', 'Partial Delivery'), ('one', 'Complete Delivery')], 'Packing Policy'),
        'order_policy': fields.selection([
            ('prepaid', 'Payment Before Delivery'),
            ('manual', 'Shipping & Manual Invoice'),
            ('postpaid', 'Invoice on Order After Delivery'),
            ('picking', 'Invoice from the Packing'),
        ], 'Shipping Policy'),
        'invoice_quantity': fields.selection([('order', 'Ordered Quantities'), ('procurement', 'Shipped Quantities')], 'Invoice on'),
        'is_auto_reconcile': fields.boolean('Auto-reconcile?', help="if true will try to reconcile the order payment statement and the open invoice"),
        'validate_order': fields.boolean('Validate Order?'),
        'validate_payment': fields.boolean('Validate Payment?'),
        'create_invoice': fields.boolean('Create Invoice?'),
        'validate_invoice': fields.boolean('Validate Invoice?'),
        'validate_picking': fields.boolean('Validate Picking?'),
        'validate_manufactoring_order': fields.boolean('Validate Manufactoring Order?'),
        'check_if_paid': fields.boolean('Check if Paid?'),
        'days_before_order_cancel': fields.integer('Days Delay before Cancel', help='number of days before an unpaid order will be cancelled at next status update from Magento'),
        'invoice_date_is_order_date' : fields.boolean('Force Invoice Date?', help="If it's check the invoice date will be the same as the order date"),
        'payment_term_id': fields.many2one('account.payment.term', 'Payment Term'),
    }
    
    _defaults = {
        'picking_policy': lambda *a: 'direct',
        'order_policy': lambda *a: 'manual',
        'invoice_quantity': lambda *a: 'order',
        'is_auto_reconcile': lambda *a: False,
        'validate_payment': lambda *a: False,
        'validate_invoice': lambda *a: False,
        'days_before_order_cancel': lambda *a: 30,
    }

base_sale_payment_type()

class account_invoice(osv.osv):
    _inherit = "account.invoice"

    def auto_reconcile(self, cr, uid, ids, context=None):
        obj_move_line = self.pool.get('account.move.line')
        for invoice in self.browse(cr, uid, ids, context=context):
            line_ids = obj_move_line.search(cr, uid, ['|', ['ref', 'ilike', invoice.origin], ['ref', '=', invoice.move_id.ref], ['account_id', '=', invoice.account_id.id]], context=context)
            if len(line_ids) == 2:
                lines = obj_move_line.read(cr, uid, line_ids, ['debit', 'credit'], context=context)
                if abs(lines[1]['debit'] - lines[0]['credit']) < 0.001 and abs(lines[0]['debit'] - lines[1]['credit']) < 0.001:
                    obj_move_line.reconcile(cr, uid, line_ids, context=context)

        return True

account_invoice()

class stock_picking(osv.osv):
    _inherit = "stock.picking"
    
    def validate_picking_from_order(self, cr, uid, order_id, context=None):
        order= self.pool.get('sale.order').browse(cr, uid, order_id, context=context)
        if not order.picking_ids:
            raise Exception('For an unknow reason the picking for the sale order %s was not created'%order.name)
        for picking in order.picking_ids:
            picking.validate_picking(context=context)
        return True

    def validate_picking(self, cr, uid, ids, context=None):
        for picking in self.browse(cr, uid, ids, context=context):
            self.force_assign(cr, uid, [picking.id])
            partial_data = {}
            for move in picking.move_lines:
                partial_data["move" + str(move.id)] = {'product_qty': move.product_qty}
            self.do_partial(cr, uid, [picking.id], partial_data)
        return True
        
    def validate_manufactoring_order(self, cr, uid, origin, context=None): #we do not create class mrp.production to avoid dependence with the module mrp
        if context == None:
            context = {}
        wf_service = netsvc.LocalService("workflow")
        mrp_prod_obj = self.pool.get('mrp.production')
        mrp_product_produce_obj = self.pool.get('mrp.product.produce')
        production_ids = mrp_prod_obj.search(cr, uid, [('origin', 'ilike', origin)])
        for production in mrp_prod_obj.browse(cr, uid, production_ids):
            mrp_prod_obj.force_production(cr, uid, [production.id])
            wf_service.trg_validate(uid, 'mrp.production', production.id, 'button_produce', cr)
            context.update({'active_model': 'mrp.production', 'active_ids': [production.id], 'search_default_ready': 1, 'active_id': production.id})
            produce = mrp_product_produce_obj.create(cr, uid, {'mode': 'consume_produce', 'product_qty': production.product_qty}, context)
            mrp_product_produce_obj.do_produce(cr, uid, [produce], context)
            self.validate_manufactoring_order(cr, uid, production.name, context)
        return True
        
stock_picking()
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
