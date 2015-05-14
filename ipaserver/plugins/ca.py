#
# Copyright (C) 2016  FreeIPA Contributors see COPYING for license
#

from ipalib import api, errors, DNParam, Str
from ipalib.constants import IPA_CA_CN
from ipalib.plugable import Registry
from ipaserver.plugins.baseldap import (
    LDAPObject, LDAPSearch, LDAPCreate, LDAPDelete,
    LDAPUpdate, LDAPRetrieve)
from ipaserver.plugins.cert import ca_enabled_check
from ipalib import _, ngettext


__doc__ = _("""
Manage Certificate Authorities

Subordinate Certificate Authorities (Sub-CAs) can be added for scoped issuance
of X.509 certificates.

EXAMPLES:

  Create new CA, subordinate to the IPA CA.

    ipa ca-add puppet --desc "Puppet" \\
        --subject "CN=Puppet CA,O=EXAMPLE.COM"

""")


register = Registry()


@register()
class ca(LDAPObject):
    """
    Lightweight CA Object
    """
    container_dn = api.env.container_ca
    object_name = _('Certificate Authority')
    object_name_plural = _('Certificate Authorities')
    object_class = ['ipaca']
    permission_filter_objectclasses = ['ipaca']
    default_attributes = [
        'cn', 'description', 'ipacaid', 'ipacaissuerdn', 'ipacasubjectdn',
    ]
    rdn_attribute = 'cn'
    rdn_is_primary_key = True
    label = _('Certificate Authorities')
    label_singular = _('Certificate Authority')

    takes_params = (
        Str('cn',
            primary_key=True,
            cli_name='name',
            label=_('Name'),
            doc=_('Name for referencing the CA'),
        ),
        Str('description?',
            cli_name='desc',
            label=_('Description'),
            doc=_('Description of the purpose of the CA'),
        ),
        Str('ipacaid',
            cli_name='id',
            label=_('Authority ID'),
            doc=_('Dogtag Authority ID'),
            flags=['no_create', 'no_update'],
        ),
        DNParam('ipacasubjectdn',
            cli_name='subject',
            label=_('Subject DN'),
            doc=_('Subject Distinguished Name'),
            flags=['no_update'],
        ),
        DNParam('ipacaissuerdn',
            cli_name='issuer',
            label=_('Issuer DN'),
            doc=_('Issuer Distinguished Name'),
            flags=['no_create', 'no_update'],
        ),
    )

    permission_filter_objectclasses = ['ipaca']
    managed_permissions = {
        'System: Read CAs': {
            'replaces_global_anonymous_aci': True,
            'ipapermbindruletype': 'all',
            'ipapermright': {'read', 'search', 'compare'},
            'ipapermdefaultattr': {
                'cn',
                'description',
                'ipacaid',
                'ipacaissuerdn',
                'ipacasubjectdn',
                'objectclass',
            },
        },
        'System: Add CA': {
            'ipapermright': {'add'},
            'replaces': [
                '(target = "ldap:///cn=*,cn=cas,cn=ca,$SUFFIX")(version 3.0;acl "permission:Add CA";allow (add) groupdn = "ldap:///cn=Add CA,cn=permissions,cn=pbac,$SUFFIX";)',
            ],
            'default_privileges': {'CA Administrator'},
        },
        'System: Delete CA': {
            'ipapermright': {'delete'},
            'replaces': [
                '(target = "ldap:///cn=*,cn=cas,cn=ca,$SUFFIX")(version 3.0;acl "permission:Delete CA";allow (delete) groupdn = "ldap:///cn=Delete CA,cn=permissions,cn=pbac,$SUFFIX";)',
            ],
            'default_privileges': {'CA Administrator'},
        },
        'System: Modify CA': {
            'ipapermright': {'write'},
            'ipapermdefaultattr': {
                'cn',
                'description',
            },
            'replaces': [
                '(targetattr = "cn || description")(target = "ldap:///cn=*,cn=cas,cn=ca,$SUFFIX")(version 3.0;acl "permission:Modify CA";allow (write) groupdn = "ldap:///cn=Modify CA,cn=permissions,cn=pbac,$SUFFIX";)',
            ],
            'default_privileges': {'CA Administrator'},
        },
    }


@register()
class ca_find(LDAPSearch):
    __doc__ = _("Search for CAs.")
    msg_summary = ngettext(
        '%(count)d CA matched', '%(count)d CAs matched', 0
    )

    def execute(self, *keys, **options):
        ca_enabled_check()
        return super(ca_find, self).execute(*keys, **options)


@register()
class ca_show(LDAPRetrieve):
    __doc__ = _("Display the properties of a CA.")

    def execute(self, *args, **kwargs):
        ca_enabled_check()
        return super(ca_show, self).execute(*args, **kwargs)


@register()
class ca_add(LDAPCreate):
    __doc__ = _("Create a CA.")
    msg_summary = _('Created CA "%(value)s"')

    def pre_callback(self, ldap, dn, entry, entry_attrs, *keys, **options):
        ca_enabled_check()
        if not ldap.can_add(dn[1:]):
            raise errors.ACIError(
                info=_("Insufficient 'add' privilege for entry '%s'.") % dn)

        # check for name collision before creating CA in Dogtag
        try:
            api.Object.ca.get_dn_if_exists(keys[-1])
            self.obj.handle_duplicate_entry(*keys)
        except errors.NotFound:
            pass

        # Create the CA in Dogtag.
        with self.api.Backend.ra_lightweight_ca as ca_api:
            resp = ca_api.create_ca(options['ipacasubjectdn'])
        entry['ipacaid'] = [resp['id']]
        entry['ipacaissuerdn'] = [resp['issuerDN']]

        # In the event that the issued certificate's subject DN
        # differs from what was requested, record the actual DN.
        #
        entry['ipacasubjectdn'] = [resp['dn']]
        return dn


@register()
class ca_del(LDAPDelete):
    __doc__ = _('Delete a CA.')

    msg_summary = _('Deleted CA "%(value)s"')

    def pre_callback(self, ldap, dn, *keys, **options):
        ca_enabled_check()

        if keys[0] == IPA_CA_CN:
            raise errors.ProtectedEntryError(
                label=_("CA"),
                key=keys[0],
                reason=_("IPA CA cannot be deleted"))

        ca_id = self.api.Command.ca_show(keys[0])['result']['ipacaid'][0]
        with self.api.Backend.ra_lightweight_ca as ca_api:
            ca_api.disable_ca(ca_id)
            ca_api.delete_ca(ca_id)

        return dn


@register()
class ca_mod(LDAPUpdate):
    __doc__ = _("Modify CA configuration.")
    msg_summary = _('Modified CA "%(value)s"')

    def pre_callback(self, ldap, dn, entry_attrs, attrs_list, *keys, **options):
        ca_enabled_check()

        if 'rename' in options or 'cn' in entry_attrs:
            if keys[0] == IPA_CA_CN:
                raise errors.ProtectedEntryError(
                    label=_("CA"),
                    key=keys[0],
                    reason=u'IPA CA cannot be renamed')

        return dn
