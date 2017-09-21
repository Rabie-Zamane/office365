#!/usr/bin/env python2.7
# -*- coding: utf-8 -*-
#
# Univention Office 365 - handle UDM calls
#
# Copyright 2016-2019 Univention GmbH
#
# http://www.univention.de/
#
# All rights reserved.
#
# The source code of this program is made available
# under the terms of the GNU Affero General Public License version 3
# (GNU AGPL V3) as published by the Free Software Foundation.
#
# Binary versions of this program provided by Univention to you as
# well as other copyrighted, protected or trademarked materials like
# Logos, graphics, fonts, specific documentations and configurations,
# cryptographic keys etc. are subject to a license agreement between
# you and Univention and not subject to the GNU AGPL V3.
#
# In the case you use this program under the terms of the GNU AGPL V3,
# the program is provided in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public
# License with the Debian GNU/Linux or Univention distribution in file
# /usr/share/common-licenses/AGPL-3; if not, see
# <http://www.gnu.org/licenses/>.

import json
import base64
import zlib
from ldap.filter import filter_format
import univention.admin.uldap
import univention.admin.objects
from univention.config_registry import ConfigRegistry
from univention.office365.logging2udebug import get_logger


logger = get_logger("office365", "o365")


class UDMHelper(object):
	"""
	UDM functions collection
	"""
	ldap_cred = None
	lo = None
	po = None
	modules = dict()

	def __init__(self, ldap_cred):
		self.__class__.ldap_cred = ldap_cred

	@classmethod
	def clean_udm_objects(cls, module_s, base, ldap_cred, tenant_filter=''):
		"""
		Remove  univentionOffice365ObjectID and univentionOffice365Data from all
		user/group objects, static for listener.clean().

		:param module_s: str: "users/user", "groups/group", etc
		:param base: str: note to start search from
		:param ldap_cred: dict: LDAP credentials collected in listeners set_data()
		:param tenant_filter: str: optional LDAP filter to remove data only
		from matching LDAP objects
		"""
		filter_s = "(&(objectClass=univentionOffice365)(|(univentionOffice365ObjectID=*)(univentionOffice365Data=*)))"
		if tenant_filter:
			filter_s = '(&{}{})'.format(filter_s, tenant_filter)
		logger.info("Cleaning %r objects with filter=%r....", module_s, filter_s)
		udm_objs = cls.find_udm_objects(module_s, filter_s, base, ldap_cred)
		for udm_obj in udm_objs:
			udm_obj.open()
			logger.info("%r...", udm_obj["username"] if "username" in udm_obj else udm_obj["name"])
			udm_obj["UniventionOffice365ObjectID"] = None
			if "UniventionOffice365Data" in udm_obj:
				udm_obj["UniventionOffice365Data"] = base64.encodestring(zlib.compress(json.dumps(None))).rstrip()
			udm_obj.modify()
		logger.info("Cleaning done.")

	@classmethod
	def find_udm_objects(cls, module_s, filter_s, base, ldap_cred):
		"""
		search LDAP for UDM objects, static for listener.clean()

		:param module_s: str: "users/user", "groups/group", etc
		:param filter_s: str: LDAP filter string
		:param base: str: node to start search from
		:param ldap_cred: dict: LDAP credentials collected in listeners set_data()
		:return: list of (not yet opened) UDM objects
		"""
		lo, po = cls._get_ldap_connection(ldap_cred)
		univention.admin.modules.update()
		module = univention.admin.modules.get(module_s)
		univention.admin.modules.init(lo, po, module)
		config = univention.admin.config.config()
		return module.lookup(config, lo, filter_s=filter_s, base=base)

	@classmethod
	def get_udm_group(cls, groupdn):
		return cls.get_udm_obj("groups/group", groupdn)

	@classmethod
	def get_udm_user(cls, userdn, attributes=None):
		return cls.get_udm_obj("users/user", userdn, attributes)

	@classmethod
	def list_udm_officeprofiles(cls, filter_s=''):
		lo, po, mod = cls.init_udm("settings/office365profile")
		return mod.lookup(None, lo, filter_s)

	@classmethod
	def get_udm_officeprofile(cls, profiledn, attributes=None):
		return cls.get_udm_obj("settings/office365profile", profiledn, attributes)

	@classmethod
	def udm_groups_with_azure_users(cls, groupdn):
		"""
		Recursively search for groups with azure users.

		:param groupdn: group to start with
		:return: list of DNs of groups that have at least one user with UniventionOffice365Enabled=1
		"""
		udm_group = cls.get_udm_group(groupdn)

		groups = list()
		for nested_groupdn in udm_group.get("nestedGroup", []):
			groups.extend(cls.udm_groups_with_azure_users(nested_groupdn))
		for userdn in udm_group.get("users", []):
			udm_user = cls.get_udm_user(userdn)
			if bool(int(udm_user.get("UniventionOffice365Enabled", "0"))):
				groups.append(groupdn)
				break
		return groups

	@classmethod
	def _get_lo_o365_objects(cls, filter_s, attributes):
		"""
		Get all LDAP group/user objects (not UDM groups/users) that are enabled for office 365 sync.

		:param filter_s: str: LDAP filter
		:param attributes: list: get only those attributes
		:return: dict: dn(str) -> attributes(dict)
		"""
		lo, po = cls._get_ldap_connection()
		logger.debug('filter_s=%r', filter_s)
		return dict(lo.search(filter_s, attr=attributes))

	@classmethod
	def get_lo_o365_users(cls, attributes=None, enabled='1', additional_filter=''):
		"""
		Get all LDAP user objects (not UDM users) that are enabled for office 365 sync.

		:param attributes: list: get only those attributes

		:param enabled: str: if the user must be enabled for office 365 use: '0': not, '1': yes, '': both
		:param additional_filter: str: will be appended to the AND clause
		:return: dict: dn(str) -> attributes(dict)
		"""
		if enabled == '':
			enabled_filter = ''
		elif enabled in ('0', '1'):
			enabled_filter = '(univentionOffice365Enabled={})'.format(enabled)
		else:
			raise ValueError("Argument 'enabled' must have value '', '0' or '1'.")
		filter_s = '(&(objectClass=posixAccount)(objectClass=univentionOffice365)(uid=*)(univentionOffice365ObjectID=*){}{})'.format(enabled_filter, additional_filter)
		logger.debug('filter_s=%r', filter_s)
		return cls._get_lo_o365_objects(filter_s, attributes)

	@classmethod
	def get_lo_o365_groups(cls, attributes=None, additional_filter=''):
		"""
		Get all LDAP user objects (not UDM users) that are enabled for office 365 sync.

		:param attributes: list: get only those attributes
		:param tenant_alias: str: get only those users for this tenant
		:param additional_filter: str: will be appended to the AND clause
		:return: dict: dn(str) -> attributes(dict)
		"""
		filter_s = '(&(objectClass=posixGroup)(objectClass=univentionOffice365)(cn=*)(univentionOffice365ObjectID=*){})'.format(additional_filter)
		return cls._get_lo_o365_objects(filter_s, attributes)

	def is_group(cls, dn):
		lo, po = cls._get_ldap_connection()
		return 'posixGroup' in lo.get(dn)['objectClass']

	@classmethod
	def is_user(cls, dn):
		lo, po = cls._get_ldap_connection()
		return 'posixAccount' in lo.get(dn)['objectClass']

	@classmethod
	def get_tenant_alias(cls, dn):
		if cls.is_user(dn):
			return cls.get_udm_user(dn).get('UniventionOffice365TenantAlias')
		elif cls.is_group(dn):
			# return cls.get_udm_group(dn).get('UniventionOffice365TenantAlias')
			raise NotImplementedError('Multi tenant support not yet available for groups.')
		else:
			raise RuntimeError('DN {!r} is neither a user nor a group.'.format(dn))

	@classmethod
	def _get_ldap_connection(cls, ldap_cred=None):
		if ldap_cred and not cls.ldap_cred:
			cls.ldap_cred = ldap_cred
		if not cls.lo or not cls.po:
			if cls.ldap_cred:
				cls.lo = univention.admin.uldap.access(
					host=cls.ldap_cred["ldapserver"],
					base=cls.ldap_cred["basedn"],
					binddn=cls.ldap_cred["binddn"],
					bindpw=cls.ldap_cred["bindpw"])
				ucr = ConfigRegistry()
				ucr.load()
				cls.po = univention.admin.uldap.position(ucr["ldap/base"])
			else:
				cls.lo, cls.po = univention.admin.uldap.getAdminConnection()
		return cls.lo, cls.po

	@classmethod
	def get_udm_obj(cls, module_name, dn, attributes=None):
		lo, po, mod = cls.init_udm(module_name)
		obj = mod.object(None, lo, po, dn, attributes=attributes)
		obj.open()
		return obj

	@classmethod
	def init_udm(cls, module_name):
		lo, po = cls._get_ldap_connection()
		try:
			mod = cls.modules[module_name]
		except KeyError:
			univention.admin.modules.update()
			mod = univention.admin.modules.get(module_name)
			univention.admin.modules.init(lo, po, mod)
			cls.modules[module_name] = mod
		return lo, po, mod
