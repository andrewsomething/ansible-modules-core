#!/usr/bin/python
# -*- coding: utf-8 -*-

# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
DOCUMENTATION = '''
---
module: digital_ocean_droplet
short_description: Create/delete a droplet/SSH_key in DigitalOcean
description:
     - Create/delete a droplet in DigitalOcean and optionally wait for it to be 'running', or deploy an SSH key.
version_added: "2.3"
author: "Andrew Starr-Bochicchio (@andrewsomething)"
options:
  command:
    description:
     - Which target you want to operate on.
    default: droplet
    choices: ['droplet', 'ssh']
  state:
    description:
     - Indicate desired state of the target.
    default: present
    choices: ['present', 'active', 'absent', 'deleted']
  api_token:
    description:
     - DigitalOcean api token.
    version_added: "2.3"
  id:
    description:
     - Numeric, the droplet id you want to operate on.
  name:
    description:
     - String, this is the name of the droplet - must be formatted by hostname rules, or the name of a SSH key.
  unique_name:
    description:
     - Bool, require unique hostnames.  By default, DigitalOcean allows multiple hosts with the same name.  Setting this to "yes" allows only one host per name.  Useful for idempotence.
    version_added: "2.3"
    default: "no"
    choices: [ "yes", "no" ]
  size_id:
    description:
     - This is the slug of the size you would like the droplet created with.
  image_id:
    description:
     - This is the slug of the image you would like the droplet created with.
  region_id:
    description:
     - This is the slug of the region you would like your server to be created in.
  ssh_key_ids:
    description:
     - Optional, array of of SSH key (numeric) ID that you would like to be added to the server.
  virtio:
    description:
     - "Bool, turn on virtio driver in droplet for improved network and storage I/O."
    version_added: "2.3"
    default: "yes"
    choices: [ "yes", "no" ]
  private_networking:
    description:
     - "Bool, add an additional, private network interface to droplet for inter-droplet communication."
    version_added: "2.3"
    default: "no"
    choices: [ "yes", "no" ]
  backups_enabled:
    description:
     - Optional, Boolean, enables backups for your droplet.
    version_added: "2.3"
    default: "no"
    choices: [ "yes", "no" ]
  user_data:
    description:
      - opaque blob of data which is made available to the droplet
    version_added: "2.3"
    required: false
    default: None
  ipv6:
    description:
      - Optional, Boolean, enable IPv6 for your droplet.
    version_added: "2.3"
    required: false
    default: "no"
    choices: [ "yes", "no" ]
  wait:
    description:
     - Wait for the droplet to be in state 'running' before returning.  If wait is "no" an ip_address may not be returned.
    default: "yes"
    choices: [ "yes", "no" ]
  wait_timeout:
    description:
     - How long before wait gives up, in seconds.
    default: 300
  ssh_pub_key:
    description:
     - The public SSH key you want to add to your account.

notes:
  - Two environment variables can be used, DO_API_KEY and DO_API_TOKEN. They both refer to the v2 token.
  - As of Ansible 1.9.5 and 2.0, Version 2 of the DigitalOcean API is used, this removes C(client_id) and C(api_key) options in favor of C(api_token).
  - If you are running Ansible 1.9.4 or earlier you might not be able to use the included version of this module as the API version used has been retired.
    Upgrade Ansible or, if unable to, try downloading the latest version of this module from github and putting it into a 'library' directory.
requirements:
  - "python >= 2.6"
'''


EXAMPLES = '''
# Create a new Droplet
# Will return the droplet details including the droplet id (used for idempotence)

- digital_ocean_droplet:
    state: present
    command: droplet
    name: mydroplet
    api_token: XXX
    size_id: 2gb
    region_id: nyc1
    image_id: ubuntu-16-04-x64
    wait_timeout: 500

  register: my_droplet

- debug: msg="ID is {{ my_droplet.droplet.id }}"
- debug: msg="IP is {{ my_droplet.droplet.ip_address }}"

# Ensure a droplet is present
# If droplet id already exist, will return the droplet details and changed = False
# If no droplet matches the id, a new droplet will be created and the droplet details (including the new id) are returned, changed = True.

- digital_ocean_droplet:
    state: present
    command: droplet
    id: 123
    name: mydroplet
    api_token: XXX
    size_id: 2gb
    region_id: nyc1
    image_id: ubuntu-16-04-x64
    wait_timeout: 500

# Create a droplet with ssh key
# The ssh key id can be passed as argument at the creation of a droplet (see ssh_key_ids).
# Several keys can be added to ssh_key_ids as id1,id2,id3
# The keys are used to connect as root to the droplet.

- digital_ocean_droplet:
    state: present
    ssh_key_ids: 123,456
    name: mydroplet
    api_token: XXX
    size_id: 2gb
    region_id: nyc1
    image_id: ubuntu-16-04-x64

'''

import json
import os
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import fetch_url


class TimeoutError(Exception):
    def __init__(self, msg):
        super(TimeoutError, self).__init__(msg)


class Response(object):

    def __init__(self, resp, info):
        self.body = None
        if resp:
            self.body = resp.read()
        self.info = info

    @property
    def json(self):
        if not self.body:
            if "body" in self.info:
                return json.loads(self.info["body"])
            return None
        try:
            return json.loads(self.body)
        except ValueError:
            return None

    @property
    def status_code(self):
        return self.info["status"]


class Rest(object):

    def __init__(self, module, headers):
        self.module = module
        self.headers = headers
        self.baseurl = 'https://api.digitalocean.com/v2'

    def _url_builder(self, path):
        if path[0] == '/':
            path = path[1:]
        return '%s/%s' % (self.baseurl, path)

    def send(self, method, path, data=None, headers=None):
        url = self._url_builder(path)

        resp, info = fetch_url(self.module, url, data=data,
                               headers=self.headers, method=method)

        return Response(resp, info)

    def get(self, path, data=None, headers=None):
        return self.send('GET', path, data, headers)

    def put(self, path, data=None, headers=None):
        return self.send('PUT', path, data, headers)

    def post(self, path, data=None, headers=None):
        return self.send('POST', path, data, headers)

    def delete(self, path, data=None, headers=None):
        return self.send('DELETE', path, data, headers)


def get_droplet(inst, droplet_id=None, name=None):
    if not droplet_id and not name:
        return False

    if droplet_id:
        droplet = inst.get("droplets/{}".format(droplet_id))
        return droplet.json['droplet']

    if name:
        droplets = inst.get("droplets")
        for droplet in droplets.json['droplets']:
            if droplet['name'] == name:
                return droplet

    return False


def is_powered_on(inst, droplet_id):
    res = get_droplet(inst, droplet_id=droplet_id)
    return res['status'] == 'active'


def power_on(inst, droplet_id):
    inst.post("droplets/{}/actions".format(droplet_id),
              data={'type': 'power_on'})


def ensure_powered_on(inst, droplet, wait=True, wait_timeout=300):
    if is_powered_on(inst, droplet['id']):
        return
    if droplet['status'] == 'off':
        power_on(inst, droplet['id'])

    if wait:
        end_time = time.time() + wait_timeout
        while time.time() < end_time:
            time.sleep(min(20, end_time - time.time()))
            if is_powered_on(inst, droplet['id']):
                return
        raise TimeoutError(
            'Timeout waiting for Droplet {} to power on.'.format(
                droplet['id']))


def core(module):
    try:
        api_token = module.params['api_token'] or \
            os.environ['DO_API_TOKEN'] or os.environ['DO_API_KEY']
    except KeyError as e:
        module.fail_json(msg='Unable to load %s' % e.message)

    changed = True
    state = module.params['state']
    rest = Rest(module, {'Authorization': 'Bearer {}'.format(api_token),
                         'Content-type': 'application/json'})

    # First, try to find a droplet by id.
    droplet = get_droplet(rest, droplet_id=module.params.get('id'))

    # If we couldn't find the droplet and the user is allowing unique
    # hostnames, then check to see if a droplet with the specified
    # hostname already exists.
    if not droplet and module.params.get('unique_name'):

        droplet = get_droplet(rest, name=module.params.get('name'))

    if state in ('active', 'present'):
        # If both of those attempts failed, then create a new droplet.
        if not droplet:
            payload = {
                'name': module.params.get('name'),
                'size': module.params.get('size_id'),
                'image': module.params.get('image_id'),
                'region': module.params.get('region_id'),
                'ssh_keys': module.params.get('ssh_key_ids'),
                'private_networking': module.params.get('private_networking'),
                'backups': module.params.get('backups_enabled'),
                'user_data': module.params.get('user_data'),
                'ipv6': module.params.get('ipv6')
            }
            res = rest.post("droplets", data=module.jsonify(payload))

            if 'message' in res.json:
                module.fail_json(msg=droplet.json)
            else:
                droplet = res.json['droplet']

        if is_powered_on(rest, droplet['id']):
            changed = False

        if changed:
            ensure_powered_on(rest, droplet,
                              wait=module.params.get('wait'),
                              wait_timeout=module.params.get('wait_timeout'))

        module.exit_json(changed=changed, droplet=droplet)

    elif state in ('absent', 'deleted'):
        if not droplet:
            module.exit_json(changed=False,
                             msg='Droplet not found.')

        rest.delete("droplets/{}".format(droplet['id']))
        module.exit_json(changed=True)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            command=dict(choices=['droplet'], default='droplet'),
            state=dict(choices=['active', 'present', 'absent', 'deleted'],
                       default='present'),
            api_token=dict(aliases=['API_TOKEN'], no_log=True),
            name=dict(type='str'),
            size_id=dict(),
            image_id=dict(),
            region_id=dict(),
            ssh_key_ids=dict(type='list'),
            private_networking=dict(type='bool', default=False),
            backups_enabled=dict(type='bool', default=False),
            id=dict(aliases=['droplet_id'], type='int'),
            unique_name=dict(type='bool', default=False),
            user_data=dict(default=None),
            ipv6=dict(type='bool', default=False),
            wait=dict(type='bool', default=True),
            wait_timeout=dict(default=300, type='int'),
            ssh_pub_key=dict(type='str'),
        ),
        required_together=(
            ['size_id', 'image_id', 'region_id'],
        ),
        required_one_of=(
            ['id', 'name'],
        ),
    )

    try:
        core(module)
    except Exception as e:
        module.fail_json(msg=str(e))


if __name__ == '__main__':
    main()
