import logging
import os

import optparse
from samba import getopt as options

from samba.gpclass import get_dc_hostname, check_refresh_gpo_list
from samba.netcmd.common import netcmd_get_domain_infos_via_cldap
import samba.gpo
import pysss_nss_idmap

from storage import cache_factory
from .xdg import get_user_dir
from .util import get_homedir

class smbcreds:
    def __init__(self, dc_fqdn=None):
        self.parser = optparse.OptionParser('GPO Applier')
        self.sambaopts = options.SambaOptions(self.parser)
        self.credopts = options.CredentialsOptions(self.parser)
        self.lp = self.sambaopts.get_loadparm()
        self.creds = self.credopts.get_credentials(self.lp, fallback_machine=True)
        self.selected_dc = self.set_dc(dc_fqdn)

    def get_dc(self):
        return self.selected_dc

    def set_dc(self, dc_fqdn):
        '''
        Force selection of the specified DC
        '''
        self.selected_dc = None

        try:
            samba_dc = get_dc_hostname(self.creds, self.lp)

            if samba_dc != dc_fqdn and dc_fqdn != None:
                logging.debug('Samba DC setting is {} and is overwritten by user setting {}'.format(samba_dc, dc))
                self.selected_dc = dc_fqdn
            else:
                self.selected_dc = samba_dc
        except:
            logging.error('Unable to determine DC hostname')

        return self.selected_dc

    def get_domain(self):
        '''
        Get current Active Directory domain name
        '''
        dns_domainname = None
        try:
            # Get CLDAP record about domain
            # Look and python/samba/netcmd/domain.py for more examples
            res = netcmd_get_domain_infos_via_cldap(self.lp, None, self.selected_dc)
            dns_domainname = res.dns_domain
            logging.info('Found domain via CLDAP: {}'.format(dns_domainname))
        except:
            logging.error('Unable to retrieve domain name via CLDAP query')

        return dns_domainname

    def get_cache_dir(self):
        return self._get_prop('cache directory')

    def get_gpos(self, username):
        '''
        Get GPO list for the specified username for the specified DC
        hostname
        '''
        gpos = list()

        try:
            ads = samba.gpo.ADS_STRUCT(self.selected_dc, self.lp, self.creds)
            if ads.connect():
                gpos = ads.get_gpo_list(username)
                logging.info('Got GPO list for {}:'.format(username))
                for gpo in gpos:
                    # These setters are taken from libgpo/pygpo.c
                    # print(gpo.ds_path) # LDAP entry
                    logging.info('{} ({})'.format(gpo.display_name, gpo.name))
                logging.info('------')

        except Exception as exc:
            logging.error('Unable to get GPO list for {} from {}'.format(username, self.selected_dc))

        return gpos

    def update_gpos(self, username):
        gpos = self.get_gpos(username)

        try:
            check_refresh_gpo_list(self.selected_dc, self.lp, self.creds, gpos)
        except Exception as exc:
            logging.error('Unable to refresh GPO list for {} from {}'.format(username, self.selected_dc))

        return gpos

    def _get_prop(self, property_name):
        return self.lp.get(property_name)

def wbinfo_getsid(domain, user):
    '''
    Get SID using wbinfo
    '''
    # This part works only on client
    username = '{}\\{}'.format(domain.upper(), user)
    sid = pysss_nss_idmap.getsidbyname(username)

    if username in sid:
        return sid[username]['sid']

    # This part works only on DC
    wbinfo_cmd = ['wbinfo', '-n', username]
    output = subprocess.check_output(wbinfo_cmd)
    sid = output.split()[0].decode('utf-8')

    return sid

def get_sid(domain, username):
    '''
    Lookup SID not only using wbinfo or sssd but also using own cache
    '''
    cached_sids = cache_factory('sid_cache')
    domain_username = '{}\\{}'.format(domain, username)
    sid = 'local-{}'.format(username)
    sid = cached_sids.get_default(domain_username, sid)

    try:
        sid = wbinfo_getsid(domain, username)
    except:
        sid = 'local-{}'.format(username)
        logging.warning('Error getting SID using wbinfo, will use cached SID: {}'.format(sid))

    logging.debug('Working with SID: {}'.format(sid))

    cached_sids.store(domain_username, sid)

    return sid

def expand_windows_var(text, username):
    '''
    Scan the line for percent-encoded variables and expand them.
    '''
    variables = dict()
    variables['HOME'] = get_homedir(username)
    variables['SystemRoot'] = '/'
    variables['DesktopDir'] = get_user_dir('DESKTOP', os.path.join(variables['HOME'], 'Desktop'))
    variables['StartMenuDir'] = None
    if not variables['StartMenuDir']:
        variables['StartMenuDir'] = '/usr/share/applications'

    result = text
    for var in variables.keys():
        result = result.replace('%{}%'.format(var), variables[var])

    return result

def transform_windows_path(text):
    '''
    Try to make Windows path look like UNIX.
    '''
    result = text

    if text.lower().endswith('chrome.exe'):
        result = 'chrome'
    
    return result
