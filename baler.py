#!/usr/bin/env python

from __future__ import absolute_import
import io
import json
import os
import os.path
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from os.path import expandvars
from threading import Timer
# http://python-future.org/compatible_idioms.html
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

# REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.getcwd()


def read_file(name):
    with open(name, 'r') as f:
        return f.read()
    return ''


def write_file(name, content):
    dir = os.path.dirname(name)
    if not os.path.exists(dir):
        os.makedirs(dir)
    with open(name, 'w') as f:
        return f.write(content)


def append_file(name, content):
    with open(name, 'a') as f:
        return f.write(content)


def write_checksum(folder, file):
    cmd = "openssl md5 {0} | sed 's/^.* //' > {0}.md5".format(file)
    subprocess.call(cmd, shell=True, cwd=folder)
    cmd = "openssl sha1 {0} | sed 's/^.* //' > {0}.sha1".format(file)
    subprocess.call(cmd, shell=True, cwd=folder)


# TODO: use unicode encoding
def read_json(name):
    try:
        with open(name, 'r') as f:
            return json.load(f, object_pairs_hook=OrderedDict)
    except IOError:
        return {}


def write_json(obj, name):
    with io.open(name, 'w') as f:
        data = json.dumps(obj, indent=2, separators=(',', ': '), ensure_ascii=False)
        f.write(data)


def call(cmd, stdin=None, cwd=REPO_ROOT):
    print(cmd)
    return subprocess.call([expandvars(cmd)], shell=True, stdin=stdin, cwd=cwd)


def die(status):
    if status:
        sys.exit(status)


def check_output(cmd, stdin=None, cwd=REPO_ROOT):
    print(cmd)
    return subprocess.check_output([expandvars(cmd)], shell=True, stdin=stdin, cwd=cwd)


def kill_proc(proc, timeout):
    timeout["value"] = True
    proc.kill()


# ref: http://stackoverflow.com/a/10768774
def run(cmd, timeout_sec, stdin=None):
    print(cmd)
    proc = subprocess.Popen(shlex.split(cmd), stdin=stdin)
    timeout = {"value": False}
    timer = Timer(timeout_sec, kill_proc, [proc, timeout])
    timer.start()
    proc.communicate()
    timer.cancel()
    return proc.returncode, timeout["value"]


# path to manifest
def pack(path, dest=REPO_ROOT):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        print('{0} is not a file'.format(path))
        die(1)
    pack_manifest(read_json(path), dest)


def pack_manifest(manifest, dest=REPO_ROOT):
    bundle = manifest['name']

    tmp = tempfile.mkdtemp()
    root = tmp + '/' + bundle
    layer_dir = root + '/__layers'
    if not os.path.isdir(layer_dir):
        os.makedirs(layer_dir)

    for img in manifest['images']:
        print(img)
        if '/' in img:
            name = img[img.rindex('/') + 1:img.rindex(':')]
        else:
            name = img[0:img.rindex(':')]
        print('Saving docker image:', img)
        d = root + '/' + name

        call('docker pull {0}'.format(img))
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        if not os.path.isdir(d):
            os.makedirs(d)

        call('docker save {0} > {1}/docker.tar'.format(img, d))
        call('tar xvf docker.tar', cwd=d)
        call('rm docker.tar', cwd=d)
        for layer in os.listdir(d):
            if os.path.isdir(d + '/' + layer):
                if os.path.isdir(layer_dir + '/' + layer):
                    call('rm {0}/{1}/layer.tar'.format(d, layer))
                else:
                    os.makedirs(layer_dir + '/' + layer)
                    call('mv {0}/{1}/layer.tar {2}/{3}/layer.tar'.format(d, layer, layer_dir, layer))

    # call('docker rmi {0}'.format(img))
    call('tar -czf {0}.tar.gz {0}'.format(bundle), cwd=tmp)
    call('mv {0}/{1}.tar.gz .'.format(tmp, bundle), cwd=dest)
    call('rm -rf {0}'.format(tmp))


# path to archive generated by pack()
def unpack(path, dest=REPO_ROOT):
    if path.startswith('https://') or path.startswith('http://'):
        o = urlparse(path)
        path = o.path[o.path.rindex('/') + 1:]
        call('wget -O {0} {1}'.format(path, o.geturl()))

    path = os.path.abspath(path)
    if not os.path.isfile(path):
        print('{0} is not a file'.format(path))
        die(1)

    bundle = os.path.basename(path)
    if '.' in bundle:
        bundle = bundle[:bundle.index('.')]

    tmp = tempfile.mkdtemp()
    print('Using temporary dir: ' + tmp)
    print('-------------------------------------------------------------------------------------')

    print('[*] Extracting archive ...')
    call('tar -xzf {0}'.format(path), cwd=tmp)
    print('_____________________________________________________________________________________')
    print('')

    root = tmp + '/' + bundle
    layer_dir = root + '/__layers'
    if not os.path.isdir(layer_dir):
        print('layers are missing')
        die(1)

    if not os.path.isdir(dest + '/' + bundle):
        os.makedirs(dest + '/' + bundle)

    for name in os.listdir(root):
        d = root + '/' + name
        if name == '__layers' or not os.path.isdir(d):
            continue
        print('')
        print('[*] Image: ' + name)
        print('-------------------------------------------------------------------------------------')
        print('[-] copying layers ...')
        for layer in read_json(d + '/manifest.json')[0]['Layers']:
            layer = layer[:-len('/layer.tar')]
            call('cp -r {0}/{1}/layer.tar {2}/{1}/layer.tar'.format(layer_dir, layer, d))
        print('')

        print('[-] repackaging image ...')
        call('tar -czf {0}/{1}/{2}.tar .'.format(dest, bundle, name), cwd=d)
        print('_____________________________________________________________________________________')
        print('')
    call('rm -rf {0}'.format(tmp))


# ref: https://github.com/kubernetes/kubernetes/blob/v1.4.6/cluster/saltbase/salt/kube-master-addons/kube-master-addons.sh
# path to archive generated by pack()
def load(path, dest=REPO_ROOT):
    if path.startswith('https://') or path.startswith('http://'):
        o = urlparse(path)
        path = o.path[o.path.rindex('/') + 1:]
        call('wget -O {0} {1}'.format(path, o.geturl()))

    path = os.path.abspath(path)
    if not os.path.isfile(path):
        print('{0} is not a file'.format(path))
        die(1)

    bundle = os.path.basename(path)
    if '.' in bundle:
        bundle = bundle[:bundle.index('.')]

    unpack(path)
    d = dest + '/' + bundle
    while True:
        restart_docker = False
        for img in os.listdir(d):
            if os.path.isfile(d + '/' + img):
                ret, timeout = run('docker load -i {0}/{1}'.format(d, img), 120)
                restart_docker = timeout or ret != 0
        if restart_docker:
            call('systemctl restart docker')
        else:
            break
    call('rm -rf {0}'.format(d))


# path to manifest
def rmi(path):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        print('{0} is not a file'.format(path))
        die(1)

    manifest = read_json(path)
    for img in manifest['images']:
        call('docker rmi {0}'.format(img))


if __name__ == "__main__":
    globals()[sys.argv[1]](*sys.argv[2:])
