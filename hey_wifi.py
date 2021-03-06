#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import sys
import time

if sys.version_info[0] < 3:
    import Queue as queue
else:
    import queue

import base64
import json
import hashlib
import threading
import signal
import subprocess
import random

import numpy as np
from Cryptodome.Cipher import AES
from Cryptodome.Util import Counter
import quiet
from voice_engine.source import Source


PROFILES = [os.path.join(os.path.dirname(os.path.abspath(__file__)), 'quiet-profiles.json'),
            '/usr/local/share/quiet/quiet-profiles.json']


class Decoder(object):
    def __init__(self, channels=1, select=0, bits_per_sample=16, profiles=None):
        self.channels = channels
        self.select = select
        self.done = None
        self.thread = None
        self.queue = queue.Queue()
        if bits_per_sample == 16:
            self.dtype = np.int16
        elif bits_per_sample == 32:
            self.dtype = np.int32
        else:
            raise ValueError(
                '{} bits per sample is not supported'.format(bits_per_sample))

        if not profiles:
            for file_path in PROFILES:
                if os.path.isfile(file_path):
                    self.profiles = file_path
                    break
            else:
                raise ValueError('no quiet-profiles.json found')

        else:
            self.profiles = profiles

    def start(self):
        self.done = False
        if not (self.thread and self.thread.is_alive()):
            self.thread = threading.Thread(target=self.run)
            self.thread.start()

    def put(self, data):
        self.queue.put(data)

    def run(self):

        decoder = quiet.Decoder(
            sample_rate=48000, profile_name='wave', profiles=self.profiles)

        while not self.done:
            audio = self.queue.get()
            audio = np.fromstring(audio, dtype=self.dtype)
            if self.channels > 1:
                audio = audio[self.select::self.channels]
            audio = audio.astype('float32')
            data = decoder.decode(audio)
            if data is not None:
                try:
                    self.on_data(data)
                except Exception as e:
                    print(e)
                    pass

    def stop(self):
        self.done = True
        self.queue.put('')
        if self.thread and self.thread.is_alive():
            self.thread.join()

    def on_data(self, data):
        print(data)


def get_ip_info():
    ip_info = subprocess.check_output(
        r"ip a show wlan0 | sed -ne 's/^[ \t]*inet[ \t]*\([0-9.]\+\)\/.*$/\1/p'", shell=True)
    ip_info = ip_info.strip()
    return ip_info


def encrypt(n, key, data):
    sha256 = hashlib.sha256()
    sha256.update(key)
    digest = sha256.digest()
    key = digest[:16]
    print((n, [c for c in key], [c for c in data]))
    aes = AES.new(key, AES.MODE_CTR,
                  counter=Counter.new(128, initial_value=n))
    encrypted = aes.encrypt(data)
    return base64.b64encode(encrypted).decode()


def main():
    src = Source(rate=48000, channels=4,
                 device_name='voicen', bits_per_sample=32)
    decoder = Decoder(channels=src.channels, select=0, bits_per_sample=32)

    def on_data(data):
        print([c for c in data])
        ssid_length = data[0]
        ssid = data[1:ssid_length + 1].tostring().decode('utf-8')
        password_length = data[ssid_length + 1]
        password = data[ssid_length + 2:ssid_length +
                        password_length + 2].tostring().decode('utf-8')
        # print(u'SSID: {}\nPassword: {}'.format(ssid, password))

        cmd = 'mosquitto_pub -t /voicen/hey_wifi -m 2'
        os.system(cmd)

        if os.system('which nmcli >/dev/null') != 0:
            print('nmcli is not found')
            return

        cmd = 'nmcli device wifi rescan'
        os.system(cmd)

        cmd = u'nmcli connection delete "{}"'.format(ssid)
        os.system(cmd)

        cmd = u'nmcli device wifi connect "{}" password "{}"'.format(ssid, password)
        if os.system(cmd) != 0:
            print('Failed to connect the Wi-Fi network')
            return

        print('Wi-Fi is connected')
        ip_info = get_ip_info()
        if not ip_info:
            print('Not find any IP address')
            return

        print(ip_info)
        decoder.done = True

        cmd = 'mosquitto_pub -t /voicen/hey_wifi -m 3'
        os.system(cmd)

        channel = int((data[-1] << 8) + data[-2])
        payload = json.dumps({'id': channel, 'data': ip_info.decode()})
        message = encrypt(channel, data, payload.encode())
        if os.system('which mosquitto_pub >/dev/null') != 0:
            print('mosquitto_pub is not found')

        cmd = 'mosquitto_pub -h q.voicen.io -u mqtt -P mqtt -q 1 -t "/voicen/hey_wifi/{}" -m "{}"'.format(
            channel, message)
        print(cmd)
        for _ in range(3):
            if os.system(cmd) == 0:
                break
        else:
            print('Failed to send message to the web page')

        print('Done')
        cmd = 'mosquitto_pub -t /voicen/hey_wifi -m 4'
        os.system(cmd)

    decoder.on_data = on_data

    src.pipeline(decoder)
    src.pipeline_start()

    while not decoder.done:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            print('exit')
            break

    src.pipeline_stop()


if __name__ == '__main__':
    main()
