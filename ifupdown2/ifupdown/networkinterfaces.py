#!/usr/bin/python
#
# Copyright 2014-2017 Cumulus Networks, Inc. All rights reserved.
# Author: Roopa Prabhu, roopa@cumulusnetworks.com
#
# networkInterfaces --
#    ifupdown network interfaces file parser
#

import re
import copy
import glob
import logging
import collections

try:
    from ifupdown2.ifupdown.iface import *
    from ifupdown2.ifupdown.utils import utils
    from ifupdown2.ifupdown.template import templateEngine
except ImportError:
    from ifupdown.iface import *
    from ifupdown.utils import utils
    from ifupdown.template import templateEngine


whitespaces = '\n\t\r '

class networkInterfaces():
    """ debian ifupdown /etc/network/interfaces file parser """

    #各协议支持的method
    _addrfams = {'inet' : ['static', 'manual', 'loopback', 'dhcp', 'dhcp6', 'ppp', 'tunnel'],
                 'inet6' : ['static', 'manual', 'loopback', 'dhcp', 'dhcp6', 'ppp', 'tunnel']}

    def __init__(self, interfacesfile='/etc/network/interfaces',
                 interfacesfileiobuf=None, interfacesfileformat='native',
                 template_enable='0', template_engine=None,
                 template_lookuppath=None):
        """This member function initializes the networkinterfaces parser object.

        Kwargs:
            **interfacesfile** (str):  path to the interfaces file (default is /etc/network/interfaces)

            **interfacesfileiobuf** (object): interfaces file io stream

            **interfacesfileformat** (str): format of interfaces file (choices are 'native' and 'json'. 'native' being the default)

            **template_engine** (str): template engine name

            **template_lookuppath** (str): template lookup path

        Raises:
            AttributeError, KeyError """

        #auto关键字指定的接口
        self.auto_ifaces = []
        self.callbacks = {}
        self.auto_all = False

        self.logger = logging.getLogger('ifupdown.' +
                    self.__class__.__name__)
        self.callbacks = {'iface_found' : None,#iface解释成功钩子
                          'validateifaceattr' : None,#iface属性校验
                          'validateifaceobj' : None}
        self.allow_classes = {}
        self.interfacesfile = interfacesfile
        self.interfacesfileiobuf = interfacesfileiobuf
        #接口配置文件格式
        self.interfacesfileformat = interfacesfileformat
        self._filestack = [self.interfacesfile]

        self._template_engine = None
        self._template_enable = template_enable
        self._template_engine_name = template_engine
        self._template_engine_path = template_lookuppath

        self._currentfile_has_template = False
        self._ws_split_regex = re.compile(r'[\s\t]\s*')

        self.errors = 0
        self.warns = 0

    @property
    def _currentfile(self):
        try:
            return self._filestack[-1]
        except:
            return self.interfacesfile

    def _parse_error(self, filename, lineno, msg):
        if lineno == -1 or self._currentfile_has_template:
            self.logger.error('%s: %s' %(filename, msg))
        else:
            self.logger.error('%s: line%d: %s' %(filename, lineno, msg))
        self.errors += 1

    def _parse_warn(self, filename, lineno, msg):
        if lineno == -1 or self._currentfile_has_template:
            self.logger.warn('%s: %s' %(filename, msg))
        else:
            self.logger.warn('%s: line%d: %s' %(filename, lineno, msg))
        self.warns += 1

    #校验配置的family及method是否正确
    def _validate_addr_family(self, ifaceobj, lineno=-1):
        for family in ifaceobj.addr_family:
            if not self._addrfams.get(family):
                self._parse_error(self._currentfile, lineno,
                                  'iface %s: unsupported address family \'%s\''
                                  % (ifaceobj.name, family))
                ifaceobj.addr_family = []
                ifaceobj.addr_method = None
                return
            if ifaceobj.addr_method:
                if ifaceobj.addr_method not in self._addrfams.get(family):
                    self._parse_error(self._currentfile, lineno,
                                      'iface %s: unsupported '
                                      'address method \'%s\''
                                      % (ifaceobj.name, ifaceobj.addr_method))
            else:
                ifaceobj.addr_method = 'static'

    #为iface的注册相应回调，例如属性校验，iface配置校验
    def subscribe(self, callback_name, callback_func):
        """This member function registers callback functions.

        Args:
            **callback_name** (str): callback function name (supported names: 'iface_found', 'validateifaceattr', 'validateifaceobj')

            **callback_func** (function pointer): callback function pointer

        Warns on error
        """

        if callback_name not in self.callbacks.keys():
            print 'warning: invalid callback ' + callback_name
            return -1

        self.callbacks[callback_name] = callback_func

    def ignore_line(self, line):
        l = line.strip(whitespaces)
        if not l or l[0] == '#':
            return 1
        return 0

    def process_allow(self, lines, cur_idx, lineno):
        allow_line = lines[cur_idx]

        words = re.split(self._ws_split_regex, allow_line)
        if len(words) <= 1:
            raise Exception('invalid allow line \'%s\' at line %d'
                            %(allow_line, lineno))

        allow_class = words[0].split('-')[1]
        ifacenames = words[1:]

        if self.allow_classes.get(allow_class):
            for i in ifacenames:
                self.allow_classes[allow_class].append(i)
        else:
                self.allow_classes[allow_class] = ifacenames
        return 0

    #执行source关键字的解析
    #lines 配置内容
    #cur_idx当前行号
    #lineno当前行号
    def process_source(self, lines, cur_idx, lineno):
        # Support regex
        self.logger.debug('processing sourced line ..\'%s\'' %lines[cur_idx])
        #获取source对应的文件
        sourced_file = re.split(self._ws_split_regex, lines[cur_idx], 2)[1]
        if sourced_file:
            #采用通配的方式，匹配命中的文件
            filenames = glob.glob(sourced_file)
            if not filenames:
                #文件不存在，如果无通配符，则告警
                if '*' not in sourced_file:
                    self._parse_warn(self._currentfile, lineno,
                            'cannot find source file %s' %sourced_file)
                return 0
            #遍历命中的文件，并读取解析（递归解析，这里有个bug,如果引用导致循环，则停不下来）
            for f in filenames:
                self.read_file(f)
        else:
            self._parse_error(self._currentfile, lineno,
                    'unable to read source line')
        return 0

    #处理auto关键字
    def process_auto(self, lines, cur_idx, lineno):
        auto_ifaces = re.split(self._ws_split_regex, lines[cur_idx])[1:]
        if not auto_ifaces:
            #auto之后必须给接口名称
            self._parse_error(self._currentfile, lineno,
                    'invalid auto line \'%s\''%lines[cur_idx])
            return 0
        for a in auto_ifaces:
            if a == 'all':
                self.auto_all = True
                break
            r = utils.parse_iface_range(a)
            if r:
                #支持range方式
                if len(r) == 3:
                    # eg swp1.[2-4], r = "swp1.", 2, 4)
                    for i in range(r[1], r[2]+1):
                        self.auto_ifaces.append('%s%d' %(r[0], i))
                elif len(r) == 4:
                    for i in range(r[1], r[2]+1):
                        # eg swp[2-4].100, r = ("swp", 2, 4, ".100")
                        self.auto_ifaces.append('%s%d%s' %(r[0], i, r[3]))
            #添加auto ifaces
            self.auto_ifaces.append(a)
        return 0

    def _add_to_iface_config(self, ifacename, iface_config, attrname,
                             attrval, lineno):
        newattrname = attrname.replace("_", "-")
        try:
            #对属性进行校验
            if not self.callbacks.get('validateifaceattr')(newattrname,
                                      attrval):
                self._parse_error(self._currentfile, lineno,
                        'iface %s: unsupported keyword (%s)'
                        %(ifacename, attrname))
                return
        except:
            pass
        attrvallist = iface_config.get(newattrname, [])
        if newattrname in ['scope', 'netmask', 'broadcast', 'preferred-lifetime']:
            # For attributes that are related and that can have multiple
            # entries, store them at the same index as their parent attribute.
            # The example of such attributes is 'address' and its related
            # attributes. since the related attributes can be optional,
            # we add null string '' in places where they are optional.
            # XXX: this introduces awareness of attribute names in
            # this class which is a violation.

            # get the index corresponding to the 'address'
            addrlist = iface_config.get('address')
            if addrlist:
                # 以上参数仅修饰最后一个address属性
                # find the index of last address element
                for i in range(0, len(addrlist) - len(attrvallist) -1):
                    attrvallist.append('')
                attrvallist.append(attrval)
                iface_config[newattrname] = attrvallist
        elif not attrvallist:
            #之前未用，直接设置
            iface_config[newattrname] = [attrval]
        else:
            #之前已有，且非特别attrname,直接append
            iface_config[newattrname].append(attrval)

    def parse_iface(self, lines, cur_idx, lineno, ifaceobj):
        lines_consumed = 0
        line_idx = cur_idx

        iface_line = lines[cur_idx].strip(whitespaces)
        iface_attrs = re.split(self._ws_split_regex, iface_line)
        #取iface指定的接口名称
        ifacename = iface_attrs[1]

        #接口名称长度检查（range除外）
        if (not utils.is_ifname_range(ifacename) and
            utils.check_ifname_size_invalid(ifacename)):
            self._parse_warn(self._currentfile, lineno,
                             '%s: interface name too long' %ifacename)

        # in cases where mako is unable to render the template
        # or incorrectly renders it due to user template
        # errors, we maybe left with interface names with
        # mako variables in them. There is no easy way to
        # recognize and warn about these. In the below check
        # we try to warn the user of such cases by looking for
        # variable patterns ('$') in interface names.
        if '$' in ifacename:
           self._parse_warn(self._currentfile, lineno,
                    '%s: unexpected characters in interface name' %ifacename)

        ifaceobj.raw_config.append(iface_line)
        iface_config = collections.OrderedDict()
        for line_idx in range(cur_idx + 1, len(lines)):
            l = lines[line_idx].strip(whitespaces)
            if self.ignore_line(l) == 1:
                #针对空行，注释行，仅加入不处理
                ifaceobj.raw_config.append(l)
                continue
            #将l打散成token
            attrs = re.split(self._ws_split_regex, l, 1)
            if self._is_keyword(attrs[0]):
                #如果此token为关键字，需要将此行回滚，iface处理结束
                line_idx -= 1
                break
            # if not a keyword, every line must have at least a key and value
            # 如注释所言，此行确认为iface的配置，要求必须至少有key及value
            if len(attrs) < 2:
                self._parse_error(self._currentfile, line_idx,
                        'iface %s: invalid syntax \'%s\'' %(ifacename, l))
                continue
            
            ifaceobj.raw_config.append(l)
            attrname = attrs[0]
            # preprocess vars (XXX: only preprocesses $IFACE for now)
            attrval = re.sub(r'\$IFACE', ifacename, attrs[1])
            #收集配置的key,value
            self._add_to_iface_config(ifacename, iface_config, attrname,
                                      attrval, line_idx+1)
        lines_consumed = line_idx - cur_idx

        # Create iface object
        if ifacename.find(':') != -1:
            ifaceobj.name = ifacename.split(':')[0]
        else:
            ifaceobj.name = ifacename

        ifaceobj.config = iface_config
        ifaceobj.generate_env()

        try:
            #设置地址类别，地址配置方式
            if iface_attrs[2]:
                ifaceobj.addr_family.append(iface_attrs[2])
            ifaceobj.addr_method = iface_attrs[3]
        except IndexError:
            # ignore
            pass
        self._validate_addr_family(ifaceobj, lineno)

        if self.auto_all or (ifaceobj.name in self.auto_ifaces):
            ifaceobj.auto = True

        classes = self.get_allow_classes_for_iface(ifaceobj.name)
        if classes:
            [ifaceobj.set_class(c) for c in classes]

        return lines_consumed       # Return next index

    def _create_ifaceobj_clone(self, ifaceobj, newifaceobjname,
                               newifaceobjtype, newifaceobjflags):
        ifaceobj_new = copy.deepcopy(ifaceobj)
        ifaceobj_new.realname = '%s' %ifaceobj.name
        ifaceobj_new.name = newifaceobjname
        ifaceobj_new.type = newifaceobjtype
        ifaceobj_new.flags = newifaceobjflags

        return ifaceobj_new

    #处理iface关键字
    def process_iface(self, lines, cur_idx, lineno):
        #解析iface配置，生成iface对象
        ifaceobj = iface()
        lines_consumed = self.parse_iface(lines, cur_idx, lineno, ifaceobj)

        range_val = utils.parse_iface_range(ifaceobj.name)
        if range_val:
            if len(range_val) == 3:
                for v in range(range_val[1], range_val[2]+1):
                    ifacename = '%s%d' %(range_val[0], v)
                    if utils.check_ifname_size_invalid(ifacename):
                        self._parse_warn(self._currentfile, lineno,
                                         '%s: interface name too long' %ifacename)
                    flags = iface.IFACERANGE_ENTRY
                    if v == range_val[1]:
                        flags |= iface.IFACERANGE_START
                    ifaceobj_new = self._create_ifaceobj_clone(ifaceobj,
                                        ifacename, ifaceobj.type, flags)
                    self.callbacks.get('iface_found')(ifaceobj_new)
            elif len(range_val) == 4:
                for v in range(range_val[1], range_val[2]+1):
                    ifacename = '%s%d%s' %(range_val[0], v, range_val[3])
                    if utils.check_ifname_size_invalid(ifacename):
                        self._parse_warn(self._currentfile, lineno,
                                         '%s: interface name too long' %ifacename)
                    flags = iface.IFACERANGE_ENTRY
                    if v == range_val[1]:
                        flags |= iface.IFACERANGE_START
                    ifaceobj_new = self._create_ifaceobj_clone(ifaceobj,
                                        ifacename, ifaceobj.type, flags)
                    self.callbacks.get('iface_found')(ifaceobj_new)
        else:
            #触发iface_found事件
            self.callbacks.get('iface_found')(ifaceobj)

        return lines_consumed       # Return next index

    def process_vlan(self, lines, cur_idx, lineno):
        ifaceobj = iface()
        lines_consumed = self.parse_iface(lines, cur_idx, lineno, ifaceobj)

        range_val = utils.parse_iface_range(ifaceobj.name)
        if range_val:
            if len(range_val) == 3:
                for v in range(range_val[1], range_val[2]+1):
                    flags = iface.IFACERANGE_ENTRY
                    if v == range_val[1]:
                        flags |= iface.IFACERANGE_START
                    ifaceobj_new = self._create_ifaceobj_clone(ifaceobj,
                                        '%s%d' %(range_val[0], v),
                                        ifaceType.BRIDGE_VLAN, flags)
                    self.callbacks.get('iface_found')(ifaceobj_new)
            elif len(range_val) == 4:
                for v in range(range_val[1], range_val[2]+1):
                    flags = iface.IFACERANGE_ENTRY
                    if v == range_val[1]:
                        flags |= iface.IFACERANGE_START
                    ifaceobj_new = self._create_ifaceobj_clone(ifaceobj,
                                        '%s%d%s' %(range_val[0], v, range_val[3]),
                                        ifaceType.BRIDGE_VLAN,
                                        flags)
                    self.callbacks.get('iface_found')(ifaceobj_new)
        else:
            ifaceobj.type = ifaceType.BRIDGE_VLAN
            self.callbacks.get('iface_found')(ifaceobj)

        return lines_consumed       # Return next index

    network_elems = { 'source'      : process_source,#加载给出的文件
                      'allow'      : process_allow,
                      'auto'        : process_auto, #支持atuo关键字，收集接口名称，支持range方式
                      'iface'       : process_iface,
                      'vlan'       : process_vlan}

    #检查str是否为network_elems成员
    def _is_keyword(self, str):
        # The additional split here is for allow- keyword
        if (str in self.network_elems.keys() or
            str.split('-')[0] == 'allow'):
            return 1
        return 0

    #获得/etc/network/interface的首token的处理函数
    def _get_keyword_func(self, str):
        tmp_str = str.split('-')[0]
        return self.network_elems.get(tmp_str)

    def get_allow_classes_for_iface(self, ifacename):
        classes = []
        for class_name, ifacenames in self.allow_classes.items():
            if ifacename in ifacenames:
                classes.append(class_name)
        return classes

    #处理加载的interfaces文件内容
    def process_interfaces(self, filedata):

        # process line continuations
        # 解续行符，合并成一行（续行符后的\n,将被d.strip()移除掉）
        filedata = ' '.join(d.strip() for d in filedata.split('\\'))

        line_idx = 0
        lines_consumed = 0
        raw_config = filedata.split('\n')
        lines = [l.strip(whitespaces) for l in raw_config]
        #遍历配置文件的每一行
        while (line_idx < len(lines)):
            #忽略空行，注释行
            if self.ignore_line(lines[line_idx]):
                line_idx += 1
                continue
            #将行配置打散成token
            words = re.split(self._ws_split_regex, lines[line_idx])
            if not words:
                #排除空行
                line_idx += 1
                continue
            # Check if first element is a supported keyword
            # 检查首token是否为支持的token
            if self._is_keyword(words[0]):
                #取token对应的解析函数
                keyword_func = self._get_keyword_func(words[0])
                #执行解析
                lines_consumed = keyword_func(self, lines, line_idx, line_idx+1)
                line_idx += lines_consumed
            else:
                self._parse_error(self._currentfile, line_idx + 1,
                        'error processing line \'%s\'' %lines[line_idx])
            line_idx += 1
        return 0

    #加载文件内容到filedata中
    def read_filedata(self, filedata):
        self._currentfile_has_template = False
        # run through template engine
        if filedata and '%' in filedata:
            try:
                if not self._template_engine:
                    self._template_engine = templateEngine(
                        template_engine=self._template_engine_name,
                        template_enable=self._template_enable,
                        template_lookuppath=self._template_engine_path)
                rendered_filedata = self._template_engine.render(filedata)
                if rendered_filedata is filedata:
                    self._currentfile_has_template = False
                else:
                    self._currentfile_has_template = True
            except Exception, e:
                self._parse_error(self._currentfile, -1,
                                  'failed to render template (%s). Continue without template rendering ...'
                                  % str(e))
                rendered_filedata = None
            if rendered_filedata:
                self.process_interfaces(rendered_filedata)
                return
        self.process_interfaces(filedata)

    def read_file(self, filename, fileiobuf=None):
        if fileiobuf:
            self.read_filedata(fileiobuf)
            return
        self._filestack.append(filename)
        self.logger.info('processing interfaces file %s' %filename)
        try:
            #加载文件内容到filedata
            with open(filename) as f:
                filedata = f.read()
        except Exception, e:
            self.logger.warn('error processing file %s (%s)',
                             filename, str(e))
            return
        self.read_filedata(filedata)
        self._filestack.pop()

    def read_file_json(self, filename, fileiobuf=None):
        if fileiobuf:
            ifacedicts = json.loads(fileiobuf, encoding="utf-8")
                              #object_hook=ifaceJsonDecoder.json_object_hook)
        elif filename:
            self.logger.info('processing interfaces file %s' %filename)
            with open(filename) as fp:
                ifacedicts = json.load(fp)
                            #object_hook=ifaceJsonDecoder.json_object_hook)

        # we need to handle both lists and non lists formats (e.g. {{}})
        if not isinstance(ifacedicts,list):
            ifacedicts = [ifacedicts]

        errors = 0
        for ifacedict in ifacedicts:
            ifaceobj = ifaceJsonDecoder.json_to_ifaceobj(ifacedict)
            if ifaceobj:
                self._validate_addr_family(ifaceobj)
                if not self.callbacks.get('validateifaceobj')(ifaceobj):
                    errors += 1
                self.callbacks.get('iface_found')(ifaceobj)
        self.errors += errors

    #加载接口配置文件
    def load(self):
        """ This member function loads the networkinterfaces file.

        Assumes networkinterfaces parser object is initialized with the
        parser arguments
        """
        if not self.interfacesfile and not self.interfacesfileiobuf:
            self.logger.warn('no terminal line stdin used or ')
            self.logger.warn('no network interfaces file defined.')
            self.warns += 1
            return

        if self.interfacesfileformat == 'json':
            return self.read_file_json(self.interfacesfile,
                                       self.interfacesfileiobuf)
        #非json格式的接口配置文件，读取它
        return self.read_file(self.interfacesfile,
                              self.interfacesfileiobuf)
