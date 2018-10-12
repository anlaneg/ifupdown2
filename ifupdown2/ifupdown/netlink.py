#!/usr/bin/python
#
# Copyright 2016-2017 Cumulus Networks, Inc. All rights reserved.
# Author: Julien Fortin, julien@cumulusnetworks.com
#

import sys
import socket

# this should force the use of the local nlmanager
sys.path.insert(0, '/usr/share/ifupdown2/')

from collections import OrderedDict

try:
    import ifupdown2.nlmanager.nlpacket
    import ifupdown2.ifupdown.ifupdownflags as ifupdownflags

    from ifupdown2.ifupdownaddons.cache import *
    from ifupdown2.ifupdownaddons.utilsbase import utilsBase

    from ifupdown2.nlmanager.nlcache import start_netlink_listener_with_cache
    from ifupdown2.nlmanager.nlmanager import Link, Address, Route, NetlinkPacket
except ImportError:
    import nlmanager.nlpacket
    import ifupdown.ifupdownflags as ifupdownflags

    from ifupdownaddons.cache import *
    from ifupdownaddons.utilsbase import utilsBase

    from nlmanager.nlcache import start_netlink_listener_with_cache
    from nlmanager.nlmanager import Link, Address, Route, NetlinkPacket


class Netlink(utilsBase):
    VXLAN_UDP_PORT = 4789

    def __init__(self, *args, **kargs):
        utilsBase.__init__(self, *args, **kargs)
        try:
            self.netlink = None
            self.cache = None
            self.link_kind_handlers = {
                'vlan': self._link_dump_info_data_vlan,
                'vrf': self._link_dump_info_data_vrf,
                'vxlan': self._link_dump_info_data_vxlan,
                'bond': self._link_dump_info_data_bond,
                'bridge': self._link_dump_info_data_bridge
            }
        except Exception as e:
            self.logger.error('cannot initialize ifupdown2\'s '
                              'netlink manager: %s' % str(e))
            raise

    def init(self):
        self.netlink = start_netlink_listener_with_cache()
        self.cache = self.netlink.cache

        try:
            from ifupdown2.ifupdownaddons.LinkUtils import LinkUtils
        except:
            from ifupdownaddons.LinkUtils import LinkUtils

        #
        # Our old linkCache got some extra information from sysfs
        # we need to fill it in the same way so nothing breaks.
        #
        try:
            ipcmd = LinkUtils()
            ipcmd._fill_bond_info(None)
            ipcmd._fill_bridge_info(None)
        except:
            pass

    @staticmethod
    def IN_MULTICAST(a):
        """
            /include/uapi/linux/in.h

            #define IN_CLASSD(a)            ((((long int) (a)) & 0xf0000000) == 0xe0000000)
            #define IN_MULTICAST(a)         IN_CLASSD(a)
        """
        return (int(a) & 0xf0000000) == 0xe0000000

    @staticmethod
    def mac_int_to_str(mac_int):
        """
        Return an integer in MAC string format: xx:xx:xx:xx:xx:xx
        """
        return ':'.join(("%012x" % mac_int)[i:i + 2] for i in range(0, 12, 2))

    def get_iface_index(self, ifacename):
        if ifupdownflags.flags.DRYRUN: return
        try:
            return self.netlink.cache.get_ifindex(ifacename)
        except Exception as e:
            raise Exception('%s: netlink: %s: cannot get ifindex: %s'
                            % (ifacename, ifacename, str(e)))

    def get_iface_name(self, ifindex):
        if ifupdownflags.flags.DRYRUN: return
        try:
            return self.netlink.cache.get_ifname(ifindex)
        except Exception as e:
            raise Exception('netlink: cannot get ifname for index %s: %s' % (ifindex, str(e)))

    # unused for now (grep -R "get_bridge_vlan")
    def get_bridge_vlan(self, ifname):
        self.logger.info('%s: netlink: /sbin/bridge -d -c -json vlan show' % ifname)
        if ifupdownflags.flags.DRYRUN: return
        try:
            pass
            #return self._nlmanager_api.vlan_get()
        except Exception as e:
            raise Exception('netlink: get bridge vlan: %s' % str(e))

    # unused for now (grep -R "bridge_set_vlan_filtering")
    def bridge_set_vlan_filtering(self, ifname, vlan_filtering):
        self.logger.info('%s: netlink: ip link set dev %s type bridge vlan_filtering %s'
                         % (ifname, ifname, vlan_filtering))
        if ifupdownflags.flags.DRYRUN: return
        try:
            ifla_info_data = {Link.IFLA_BR_VLAN_FILTERING: int(vlan_filtering)}
            #return self._nlmanager_api.link_set_attrs(ifname, 'bridge', ifla_info_data=ifla_info_data)
        except Exception as e:
            raise Exception('%s: cannot set %s vlan_filtering %s' % (ifname, ifname, vlan_filtering))

    def link_add_set(self,
                     ifname=None, ifindex=0,
                     kind=None, slave_kind=None,
                     ifla={},
                     ifla_info_data={},
                     ifla_info_slave_data={},
                     link_exists=False):
        """
        if the link doesn't exists we need to wait for the cache add
        otherwise we might face problem. For example if you create a
        bridge but dont wait for the object to be cached, when checking
        for vlan_filtering you'll be in trouble. On the other hand if the
        device already exists when calling netlink.link_add_set() it
        probably means that we checked the cache and all to see what
        needed to be changed etc. so its kinda safe to say that we dont
        have to wait for the cache. We do so because if you try to reset
        some values that are already running (current ifupdown2 is a bit
        broken and tries to reset some configured value I think) in that
        case, since nothing changes the kernel dont send notifications
        and ifupdown2 is in a dead-lock state (sleeping in
        threading.event.wait() to be notify and wake up...)
        """
        action = 'set' if ifindex or link_exists else 'add'

        if slave_kind:
            self.logger.info('%s: netlink: ip link set dev %s: %s slave attributes' % (ifname, ifname, slave_kind))
        else:
            self.logger.info('%s: netlink: ip link %s %s type %s with attributes' % (ifname, action, ifname, kind))
        if ifla:
            self.logger.debug('%s: ifla attributes a %s' % (ifname, ifla))
        if ifla_info_data:
            self.logger.debug('%s: ifla_info_data %s' % (ifname, ifla_info_data))
        if ifla_info_slave_data:
            self.logger.debug('%s: ifla_info_slave_data %s' % (ifname, ifla_info_slave_data))

        if ifupdownflags.flags.DRYRUN: return
        try:
            if link_exists:
                self.netlink._link_add_set(
                    ifname=ifname,
                    ifindex=ifindex,
                    kind=kind,
                    ifla=ifla,
                    slave_kind=slave_kind,
                    ifla_info_data=ifla_info_data,
                    ifla_info_slave_data=ifla_info_slave_data
                )
            else:
                self.netlink._link_add(
                    ifindex=ifindex,
                    ifname=ifname,
                    kind=kind,
                    ifla_info_data=ifla_info_data
                )
        except Exception as e:
            if kind and not slave_kind:
                kind_str = kind
            elif kind and slave_kind:
                kind_str = '%s (%s slave)' % (kind, slave_kind)
            else:
                kind_str = '(%s slave)' % slave_kind

            raise Exception('netlink: cannot %s %s %s with options: %s' % (action, kind_str, ifname, str(e)))

    def link_del(self, ifname):
        self.logger.info('%s: netlink: ip link del %s' % (ifname, ifname))
        if ifupdownflags.flags.DRYRUN: return
        try:
            self.netlink._link_del(ifname=ifname)
        except Exception as e:
            raise Exception('netlink: cannot delete link %s: %s' % (ifname, str(e)))

    def link_set_master(self, ifacename, master_dev, state=None):
        self.logger.info('%s: netlink: ip link set dev %s master %s %s'
                         % (ifacename, ifacename, master_dev,
                            state if state else ''))
        if ifupdownflags.flags.DRYRUN: return
        try:
            master = 0 if not master_dev else self.get_iface_index(master_dev)
            return self.netlink._link_set_master(ifacename,
                                                       master_ifindex=master,
                                                       state=state)
        except Exception as e:
            raise Exception('netlink: %s: cannot set %s master %s: %s'
                            % (ifacename, ifacename, master_dev, str(e)))

    def link_set_nomaster(self, ifacename, state=None):
        self.logger.info('%s: netlink: ip link set dev %s nomaster %s'
                         % (ifacename, ifacename, state if state else ''))
        if ifupdownflags.flags.DRYRUN: return
        try:
            return self.netlink._link_set_master(ifacename,
                                                       master_ifindex=0,
                                                       state=state)
        except Exception as e:
            raise Exception('netlink: %s: cannot set %s nomaster: %s'
                            % (ifacename, ifacename, str(e)))

    def link_add_vlan(self, vlanrawdevice, ifacename, vlanid, vlan_protocol):
        if vlan_protocol:
            self.logger.info('%s: netlink: ip link add link %s name %s type vlan id %s protocol %s'
                             % (ifacename, vlanrawdevice, ifacename, vlanid, vlan_protocol))

        else:
            self.logger.info('%s: netlink: ip link add link %s name %s type vlan id %s'
                             % (ifacename, vlanrawdevice, ifacename, vlanid))
        if ifupdownflags.flags.DRYRUN: return
        ifindex = self.get_iface_index(vlanrawdevice)
        try:
            return self.netlink._link_add_vlan(ifindex, ifacename, vlanid, vlan_protocol)
        except Exception as e:
            raise Exception('netlink: %s: cannot create vlan %s: %s'
                            % (vlanrawdevice, vlanid, str(e)))

    def link_add_macvlan(self, ifacename, macvlan_ifacename):
        self.logger.info('%s: netlink: ip link add link %s name %s type macvlan mode private'
                         % (ifacename, ifacename, macvlan_ifacename))
        if ifupdownflags.flags.DRYRUN: return
        ifindex = self.get_iface_index(ifacename)
        try:
            return self.netlink._link_add_macvlan(ifindex, macvlan_ifacename)
        except Exception as e:
            raise Exception('netlink: %s: cannot create macvlan %s: %s'
                            % (ifacename, macvlan_ifacename, str(e)))

    def link_set_updown_and_update_cache(self, ifname, state):
        self.link_set_updown(ifname, state)
        # if we reach this code it means the operation went through
        # without exception we can update the cache value
        # this is needed for the following case (and probably others):
        #
        # ifdown bond0 (slaves are also downed)
        # ifup bond0
        #       at the beginning the slaves are admin down
        #       ifupdownmain:run_up link up the slave
        #       the bond addon check if the slave is up or down
        #           and admin down the slave before enslavement
        #           but the cache didn't process the UP notification yet
        #           so the cache has a stale value and we try to enslave
        #           a port, that is admin up, to a bond resulting
        #           in an unexpected failure
        # TODO: dont override all the flags just turn on/off IFF_UP
        if_flags = Link.IFF_UP if state == 'up' else 0
        try:
            with self.netlink.cache._cache_lock:
                self.netlink.cache._link_cache[ifname].flags = if_flags
        except:
            pass

    def link_set_updown(self, ifacename, state):
        self.logger.info('%s: netlink: ip link set dev %s %s'
                         % (ifacename, ifacename, state))
        if ifupdownflags.flags.DRYRUN: return
        try:
            return self.netlink._link_set_updown(ifacename, state)
        except Exception as e:
            raise Exception('netlink: cannot set link %s %s: %s'
                            % (ifacename, state, str(e)))

    def link_set_protodown(self, ifacename, state):
        self.logger.info('%s: netlink: set link %s protodown %s'
                         % (ifacename, ifacename, state))
        if ifupdownflags.flags.DRYRUN: return
        try:
            return self.netlink._link_set_protodown(ifacename, state)
        except Exception as e:
            raise Exception('netlink: cannot set link %s protodown %s: %s'
                            % (ifacename, state, str(e)))

    def link_add_bridge(self, ifname):
        self.logger.info('%s: netlink: ip link add %s type bridge' % (ifname, ifname))
        if ifupdownflags.flags.DRYRUN: return
        try:
            return self.netlink._link_add_bridge(ifname)
        except Exception as e:
            raise Exception('netlink: cannot create bridge %s: %s' % (ifname, str(e)))

    def link_add_bridge_vlan(self, ifacename, vlanid):
        self.logger.info('%s: netlink: bridge vlan add vid %s dev %s'
                         % (ifacename, vlanid, ifacename))
        if ifupdownflags.flags.DRYRUN: return
        ifindex = self.get_iface_index(ifacename)
        try:
            return self.netlink._link_add_bridge_vlan(ifindex, vlanid)
        except Exception as e:
            raise Exception('netlink: %s: cannot create bridge vlan %s: %s'
                            % (ifacename, vlanid, str(e)))

    def link_del_bridge_vlan(self, ifacename, vlanid):
        self.logger.info('%s: netlink: bridge vlan del vid %s dev %s'
                         % (ifacename, vlanid, ifacename))
        if ifupdownflags.flags.DRYRUN: return
        ifindex = self.get_iface_index(ifacename)
        try:
            return self.netlink._link_del_bridge_vlan(ifindex, vlanid)
        except Exception as e:
            raise Exception('netlink: %s: cannot remove bridge vlan %s: %s'
                            % (ifacename, vlanid, str(e)))

    def link_add_vxlan(self, ifacename, vxlanid, local=None, dstport=VXLAN_UDP_PORT,
                       group=None, learning=True, ageing=None, physdev=None):
        cmd = 'ip link add %s type vxlan id %s dstport %s' % (ifacename,
                                                              vxlanid,
                                                              dstport)
        cmd += ' local %s' % local if local else ''
        cmd += ' ageing %s' % ageing if ageing else ''
        cmd += ' remote %s' % group if group else ' noremote'
        cmd += ' nolearning' if not learning else ''
        cmd += ' dev %s' % physdev if physdev else ''
        self.logger.info('%s: netlink: %s' % (ifacename, cmd))
        if ifupdownflags.flags.DRYRUN: return
        try:
            if physdev:
                physdev = self.get_iface_index(physdev)
            return self.netlink._link_add_vxlan(ifacename,
                                                      vxlanid,
                                                      dstport=dstport,
                                                      local=local,
                                                      group=group,
                                                      learning=learning,
                                                      ageing=ageing,
                                                      physdev=physdev)
        except Exception as e:
            raise Exception('netlink: %s: cannot create vxlan %s: %s'
                            % (ifacename, vxlanid, str(e)))

    @staticmethod
    def _link_dump_attr(link, ifla_attributes, dump):
        for obj in ifla_attributes:
            attr = link.attributes.get(obj['attr'])
            if attr:
                dump[obj['name']] = attr.get_pretty_value(obj=obj.get('func'))

    @staticmethod
    def _link_dump_linkdata_attr(linkdata, ifla_linkdata_attr, dump):
        for obj in ifla_linkdata_attr:
            attr = obj['attr']
            if attr in linkdata:
                func    = obj.get('func')
                value   = linkdata.get(attr)

                if func:
                    value = func(value)

                if value or obj['accept_none']:
                    dump[obj['name']] = value

    ifla_attributes = [
        {
            'attr': Link.IFLA_LINK,
            'name': 'link',
            'func': lambda x: netlink.get_iface_name(x) if x > 0 else None
        },
        {
            'attr': Link.IFLA_MASTER,
            'name': 'master',
            'func': lambda x: netlink.get_iface_name(x) if x > 0 else None
        },
        {
            'attr': Link.IFLA_IFNAME,
            'name': 'ifname',
            'func': str,
        },
        {
            'attr': Link.IFLA_MTU,
            'name': 'mtu',
            'func': str
        },
        {
            'attr': Link.IFLA_OPERSTATE,
            'name': 'state',
            'func': lambda x: '0%x' % int(x) if x > len(Link.oper_to_string) else Link.oper_to_string[x][8:]
        },
        {
            'attr': Link.IFLA_AF_SPEC,
            'name': 'af_spec',
            'func': dict
        }
    ]

    ifla_address = {'attr': Link.IFLA_ADDRESS, 'name': 'hwaddress', 'func': str}

    ifla_vxlan_attributes = [
        {
            'attr': Link.IFLA_VXLAN_LOCAL,
            'name': 'local',
            'func': str,
            'accept_none': True
        },
        {
            'attr': Link.IFLA_VXLAN_LOCAL6,
            'name': 'local',
            'func': str,
            'accept_none': True
        },
        {
            'attr': Link.IFLA_VXLAN_GROUP,
            'name': 'svcnode',
            'func': lambda x: str(x) if not Netlink.IN_MULTICAST(x) else None,
            'accept_none': False
        },
        {
            'attr': Link.IFLA_VXLAN_GROUP6,
            'name': 'svcnode',
            'func': lambda x: str(x) if not Netlink.IN_MULTICAST(x) else None,
            'accept_none': False
        },
        {
            'attr': Link.IFLA_VXLAN_LEARNING,
            'name': 'learning',
            'func': lambda x: 'on' if x else 'off',
            'accept_none': True
        }
    ]

    def _link_dump_info_data_vlan(self, ifname, linkdata):
        return {
            'vlanid': str(linkdata.get(Link.IFLA_VLAN_ID, '')),
            'vlan_protocol': linkdata.get(Link.IFLA_VLAN_PROTOCOL)
        }

    def _link_dump_info_data_vrf(self, ifname, linkdata):
        vrf_info = {'table': str(linkdata.get(Link.IFLA_VRF_TABLE, ''))}

        # to remove later when moved to a true netlink cache
        linkCache.vrfs[ifname] = vrf_info
        return vrf_info

    def _link_dump_info_data_vxlan(self, ifname, linkdata):
        for attr, value in (
                ('learning', 'on'),
                ('svcnode', None),
                ('vxlanid', str(linkdata.get(Link.IFLA_VXLAN_ID, ''))),
                ('ageing', str(linkdata.get(Link.IFLA_VXLAN_AGEING, ''))),
                (Link.IFLA_VXLAN_PORT, linkdata.get(Link.IFLA_VXLAN_PORT))
        ):
            linkdata[attr] = value
        self._link_dump_linkdata_attr(linkdata, self.ifla_vxlan_attributes, linkdata)
        return linkdata

    ifla_bond_attributes = (
        Link.IFLA_BOND_MODE,
        Link.IFLA_BOND_MIIMON,
        Link.IFLA_BOND_USE_CARRIER,
        Link.IFLA_BOND_AD_LACP_RATE,
        Link.IFLA_BOND_XMIT_HASH_POLICY,
        Link.IFLA_BOND_MIN_LINKS,
        Link.IFLA_BOND_NUM_PEER_NOTIF,
        Link.IFLA_BOND_AD_ACTOR_SYSTEM,
        Link.IFLA_BOND_AD_ACTOR_SYS_PRIO,
        Link.IFLA_BOND_AD_LACP_BYPASS,
        Link.IFLA_BOND_UPDELAY,
        Link.IFLA_BOND_DOWNDELAY,
    )

    def _link_dump_info_data_bond(self, ifname, linkdata):
        linkinfo = {}
        for nl_attr in self.ifla_bond_attributes:
            try:
                linkinfo[nl_attr] = linkdata.get(nl_attr)
            except Exception as e:
                self.logger.debug('%s: parsing bond IFLA_INFO_DATA (%s): %s'
                                  % (ifname, nl_attr, str(e)))
        return linkinfo

    # this dict contains the netlink attribute, cache key,
    # and a callable to translate the netlink value into
    # whatever value we need to store in the old cache to
    # make sure we don't break anything
    ifla_bridge_attributes = (
        (Link.IFLA_BR_UNSPEC, Link.IFLA_BR_UNSPEC, None),
        (Link.IFLA_BR_FORWARD_DELAY, "fd", lambda x: str(x / 100)),
        (Link.IFLA_BR_HELLO_TIME, "hello", lambda x: str(x / 100)),
        (Link.IFLA_BR_MAX_AGE, "maxage", lambda x: str(x / 100)),
        (Link.IFLA_BR_AGEING_TIME, "ageing", lambda x: str(x / 100)),
        (Link.IFLA_BR_STP_STATE, "stp", lambda x: 'yes' if x else 'no'),
        (Link.IFLA_BR_PRIORITY, "bridgeprio", str),
        (Link.IFLA_BR_VLAN_FILTERING, 'vlan_filtering', str),
        (Link.IFLA_BR_VLAN_PROTOCOL, "vlan-protocol", str),
        (Link.IFLA_BR_GROUP_FWD_MASK, Link.IFLA_BR_GROUP_FWD_MASK, None),
        (Link.IFLA_BR_ROOT_ID, Link.IFLA_BR_ROOT_ID, None),
        (Link.IFLA_BR_BRIDGE_ID, Link.IFLA_BR_BRIDGE_ID, None),
        (Link.IFLA_BR_ROOT_PORT, Link.IFLA_BR_ROOT_PORT, None),
        (Link.IFLA_BR_ROOT_PATH_COST, Link.IFLA_BR_ROOT_PATH_COST, None),
        (Link.IFLA_BR_TOPOLOGY_CHANGE, Link.IFLA_BR_TOPOLOGY_CHANGE, None),
        (Link.IFLA_BR_TOPOLOGY_CHANGE_DETECTED, Link.IFLA_BR_TOPOLOGY_CHANGE_DETECTED, None),
        (Link.IFLA_BR_HELLO_TIMER, Link.IFLA_BR_HELLO_TIMER, None),
        (Link.IFLA_BR_TCN_TIMER, Link.IFLA_BR_TCN_TIMER, None),
        (Link.IFLA_BR_TOPOLOGY_CHANGE_TIMER, Link.IFLA_BR_TOPOLOGY_CHANGE_TIMER, None),
        (Link.IFLA_BR_GC_TIMER, Link.IFLA_BR_GC_TIMER, None),
        (Link.IFLA_BR_GROUP_ADDR, Link.IFLA_BR_GROUP_ADDR, None),
        (Link.IFLA_BR_FDB_FLUSH, Link.IFLA_BR_FDB_FLUSH, None),
        (Link.IFLA_BR_MCAST_ROUTER, "mcrouter", str),
        (Link.IFLA_BR_MCAST_SNOOPING, "mcsnoop", str),
        (Link.IFLA_BR_MCAST_QUERY_USE_IFADDR, "mcqifaddr", str),
        (Link.IFLA_BR_MCAST_QUERIER, "mcquerier", str),
        (Link.IFLA_BR_MCAST_HASH_ELASTICITY, "hashel", str),
        (Link.IFLA_BR_MCAST_HASH_MAX, "hashmax", str),
        (Link.IFLA_BR_MCAST_LAST_MEMBER_CNT, "mclmc", str),
        (Link.IFLA_BR_MCAST_STARTUP_QUERY_CNT, "mcsqc", str),
        (Link.IFLA_BR_MCAST_LAST_MEMBER_INTVL, "mclmi", lambda x: str(x / 100)),
        (Link.IFLA_BR_MCAST_MEMBERSHIP_INTVL, "mcmi", lambda x: str(x / 100)),
        (Link.IFLA_BR_MCAST_QUERIER_INTVL, "mcqpi", lambda x: str(x / 100)),
        (Link.IFLA_BR_MCAST_QUERY_INTVL, "mcqi", lambda x: str(x / 100)),
        (Link.IFLA_BR_MCAST_QUERY_RESPONSE_INTVL, "mcqri", lambda x: str(x / 100)),
        (Link.IFLA_BR_MCAST_STARTUP_QUERY_INTVL, "mcsqi", lambda x: str(x / 100)),
        (Link.IFLA_BR_NF_CALL_IPTABLES, Link.IFLA_BR_NF_CALL_IPTABLES, None),
        (Link.IFLA_BR_NF_CALL_IP6TABLES, Link.IFLA_BR_NF_CALL_IP6TABLES, None),
        (Link.IFLA_BR_NF_CALL_ARPTABLES, Link.IFLA_BR_NF_CALL_ARPTABLES, None),
        (Link.IFLA_BR_VLAN_DEFAULT_PVID, Link.IFLA_BR_VLAN_DEFAULT_PVID, None),
        (Link.IFLA_BR_PAD, Link.IFLA_BR_PAD, None),
        (Link.IFLA_BR_VLAN_STATS_ENABLED, "vlan-stats", str),
        (Link.IFLA_BR_MCAST_STATS_ENABLED, "mcstats", str),
        (Link.IFLA_BR_MCAST_IGMP_VERSION, "igmp-version", str),
        (Link.IFLA_BR_MCAST_MLD_VERSION, "mld-version", str)
    )

    def _link_dump_info_data_bridge(self, ifname, linkdata):
        linkinfo = {}
        for nl_attr, cache_key, func in self.ifla_bridge_attributes:
            try:
                if func:
                    linkinfo[cache_key] = func(linkdata.get(nl_attr))
                else:
                    linkinfo[cache_key] = linkdata.get(nl_attr)

                # we also store the value in pure netlink,
                # to make the transition easier in the future
                linkinfo[nl_attr] = linkdata.get(nl_attr)
            except Exception as e:
                self.logger.error('%s: parsing birdge IFLA_INFO_DATA %s: %s'
                                  % (ifname, nl_attr, str(e)))
        return linkinfo

    def _link_dump_info_slave_data_bridge(self, ifname, info_slave_data):
        return info_slave_data

    def _link_dump_linkinfo(self, link, dump):
        linkinfo = link.attributes[Link.IFLA_LINKINFO].get_pretty_value(dict)

        if linkinfo:
            info_kind = linkinfo.get(Link.IFLA_INFO_KIND)
            info_data = linkinfo.get(Link.IFLA_INFO_DATA)

            info_slave_kind = linkinfo.get(Link.IFLA_INFO_SLAVE_KIND)
            info_slave_data = linkinfo.get(Link.IFLA_INFO_SLAVE_DATA)

            dump['kind']        = info_kind
            dump['slave_kind']  = info_slave_kind

            if info_data:
                link_kind_handler = self.link_kind_handlers.get(info_kind)
                if callable(link_kind_handler):
                    dump['linkinfo'] = link_kind_handler(dump['ifname'], info_data)

            if info_slave_data:
                dump['info_slave_data'] = info_slave_data

    def link_dump(self, ifname=None):
        if ifname:
            self.logger.info('netlink: ip link show dev %s' % ifname)
        else:
            self.logger.info('netlink: ip link show')

        if ifupdownflags.flags.DRYRUN: return {}

        links = dict()

        try:
            if ifname:
                links_dump_list = [self.netlink.cache.get_link_obj(ifname, True)]
            else:
                links_dump_list = self.netlink.cache._link_cache.values()
        except Exception as e:
            raise Exception('netlink: link dump failed: %s' % str(e))

        for link in links_dump_list:
            try:
                dump = dict()

                flags = []
                for flag, string in Link.flag_to_string.items():
                    if link.flags & flag:
                        flags.append(string[4:])

                dump['flags'] = flags
                dump['ifflag'] = 'UP' if 'UP' in flags else 'DOWN'
                dump['ifindex'] = str(link.ifindex)

                if link.device_type == Link.ARPHRD_ETHER:
                    self._link_dump_attr(link, [self.ifla_address], dump)

                self._link_dump_attr(link, self.ifla_attributes, dump)

                if Link.IFLA_LINKINFO in link.attributes:
                    self._link_dump_linkinfo(link, dump)

                links[dump['ifname']] = dump
            except Exception as e:
                self.logger.warning('netlink: ip link show: %s' % str(e))
        return links


netlink = Netlink()