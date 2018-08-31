from boto.ec2 import EC2Connection, get_region
import boto.exception
import logging
import subprocess
import urllib2
from mako.template import Template
from datetime import datetime

__version__ = '0.5.1'


def get_self_instance_id():
    '''
    Get this instance's id.
    '''
    logging.debug('get_self_instance_id()')
    response = urllib2.urlopen('http://169.254.169.254/1.0/meta-data/instance-id')
    instance_id = response.read()
    return instance_id


def steal_elastic_ip(access_key=None, secret_key=None, ip=None):
    '''
    Assign an elastic IP to this instance.
    '''
    logging.debug('steal_elastic_ip()')
    instance_id = get_self_instance_id()
    conn = EC2Connection(aws_access_key_id=access_key,
                         aws_secret_access_key=secret_key)
    conn.associate_address(instance_id=instance_id, public_ip=ip)


def get_running_instances(access_key=None, secret_key=None, security_group=None, region=None, safe_mode=False, delay=0):
    '''
    Get all running instances. Only within a security group if specified.
    '''
    logging.debug('get_running_instances()')

    instances_all_regions_list = []
    if region is None:
        conn = EC2Connection(aws_access_key_id=access_key,
                             aws_secret_access_key=secret_key)
        ec2_region_list = conn.get_all_regions()
    else:
        ec2_region_list = [get_region(region)]

    for region in ec2_region_list:
        conn = EC2Connection(aws_access_key_id=access_key,
                             aws_secret_access_key=secret_key,
                             region=region)

        running_instances = []
        date_format = '%Y-%m-%dT%H:%M:%S.%fZ'
        try:
            for s in conn.get_all_security_groups():
                if s.name == security_group:
                    running_instances.extend([
                        i for i in s.instances()
                        if i.state == 'running' and (
                            datetime.utcnow() - datetime.strptime(i.launch_time, date_format)).seconds > delay
                    ])
        except boto.exception.EC2ResponseError:
            logging.error('Region [' + region.name + '] inaccessible')
            if safe_mode:
                logging.error('Safe mode enabled. No new haproxy cfg is generated. Exit now.')
                exit(1)
        if running_instances:
            for instance in running_instances:
                logging.info(instance)
                print(instance)
                print("============================")
                instances_all_regions_list.append(instance)
        logging.info("**********************************")
        logging.info(instances_all_regions_list)
    	instances_all_regions_list_sorted=sorted(instances_all_regions_list)
	print(instances_all_regions_list_sorted)
    return instances_all_regions_list_sorted

def exists_empty_security_group(instances):
    for sg, instances in instances.iteritems():
        if not instances:
            logging.error('There is no instance in security group %s', sg)
            return True
    return False


def file_contents(filename=None, content=None):
    '''
    Just return the contents of a file as a string or write if content
    is specified. Returns the contents of the filename either way.
    '''
    logging.debug('file_contents()')
    if content:
        f = open(filename, 'w')
        f.write(content)
        f.close()

    try:
        f = open(filename, 'r')
        text = f.read()
        f.close()
    except:
        text = None

    return text


def generate_haproxy_config(template=None, instances=None):
    '''
    Generate an haproxy configuration based on the template and instances list.
    '''
    instances=sorted(instances)
    print("-+-+-+-+-+-+-+-+-")
    print(instances)
    print("-+-+-+-+-+-+-+-+-")
    return Template(filename=template).render(instances=instances)


def reload_haproxy(args):
    '''
    Reload haproxy, either by an Ubuntu service or standalone binary
    '''
    logging.info('Reloading haproxy.')

    if args.haproxy:
        # Get PID if haproxy is already running.
        logging.debug('Fetching PID from %s.', args.pid)
        pid = file_contents(filename=args.pid)
        command = '''%s -p %s -f %s -sf %s''' % (args.haproxy, args.pid, args.output, pid or '')

    else:
        command = "/sbin/service %s reload" % args.servicename

    logging.debug('Executing: %s', command)
    subprocess.call(command, shell=True)


class Backends(object):
    """
    this class is used for the tests functionality    
    """

    # instances without these tags will be excluded from backends
    required_keys = ['AppName',
                     'AppPort']

    backend_templates = {'default': {'mode': 'http',
                                     'option': 'httpchk',
                                     'balance': 'roundrobin'},
                         'ssl-backend': {'mode': 'https',
                                         'option': 'httpchk',
                                         'balance': 'roundrobin'}}
    comment = ("# Autogenerated with haproxy_autoscale version %s"
               % __version__)

    def get_acls(self, instances_dict, tabindent, domain, prefixes=None):
        """Generate neatly printed cfg-worthy backends for haproxy.

            Args:
                instances_dict: haproxy_autoscale.get_running_instances return.
                tabindent: Int, number of spaces to prepend hanging config lines.
                domain: Str, TLD to serve all backends from.
                prefixes: List, strings to prepend to acls and backends.
            Returns:
                return_comment: Str, version comment information.
        """

        # flatten all security group lookups into single instance list
        self.all_instances = []
        for instance_list in instances_dict.values():
            for instance in instance_list:
                self.all_instances.append(instance)

        self.all_backends = []
        self.included_instances = []
        self.excluded_instances = []

        if type(prefixes) is list:
            for prefix in prefixes:
                self.required_keys.append(prefix)
        else:
            prefixes = []

        for instance in self.all_instances:
            instance.missing_tags = []
            for key in self.required_keys:
                if key not in instance.tags:
                    instance.missing_tags.append(key)
            if len(instance.missing_tags) is 0:
                self.included_instances.append(instance)
                app_name = instance.tags['AppName']
                prefix_str = ''
                if len(prefixes) > 0:
                    for prefix in prefixes:
                        prefix_str = prefix_str + "%s-" % instance.tags[prefix]

                backend_name = "%s%s" % (prefix_str, app_name)
                instance.tags['backend'] = backend_name
                if backend_name not in self.all_backends:
                    self.all_backends.append(backend_name)
            else:
                self.excluded_instances.append(instance)

        # generate acls and redirects
        tabindent_str = (' ' * tabindent)
        return_str = "\n%s%s" % (tabindent_str, self.comment)
        for backend in self.all_backends:
            return_str = return_str + ("\n%sacl %s hdr(host) -i %s.%s"
                                       % (tabindent_str,
                                          backend,
                                          backend,
                                          domain))
            return_str = return_str + ("\n%suse_backend %s if %s"
                                       % (tabindent_str,
                                          backend,
                                          backend))
        return return_str

    def generate(self, template_name, tabindent, cookie=True):
        """Iterate over all backend objects and generate default backend.
        
        Args:
            template_name: Str, a haproxy_autoscale.Backends.backend_templates.
            tabindent: Int, number of spaces to prepend hanging config lines.
            cookie: Bool, False to disabled sticky sessions.
        Returns:
            return_str: Str, formatted haproxy backend text block.
        """

        template = self.backend_templates.get(template_name)
        tabindent_str = (' ' * tabindent)
        return_str = ''

        # generate backend cfg from template
        for backend in self.all_backends:
            return_str = return_str + ("\n\n%s\nbackend %s"
                                       % (self.comment, backend))
            for key, value in template.iteritems():
                return_str = return_str + "\n%s%s %s" % (tabindent_str,
                                                         key, value)
            if cookie is True:
                return_str = return_str + ("\n%scookie SERVERID insert"
                                           " indirect nocache" % tabindent_str)
            return_str = return_str + "\n"

            # populate backend with instances
            for instance in self.included_instances:
                if instance.tags['backend'] == backend:
                    return_str = return_str + ("\n%sserver %s %s:%s"
                                               % (tabindent_str,
                                                  instance.id,
                                                  instance.private_dns_name,
                                                  instance.tags['AppPort']))
                    if cookie is True:
                        return_str = return_str + " cookie %s" % (instance.id)

        return return_str
