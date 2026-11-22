"""Thread-safe iPad pattern controller with render acknowledgement."""
from __future__ import annotations
import threading
import time

class PatternController:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = {'freqs':[1,4,16], 'brightness':128, 'contrast':100,
                       'screen_width_mm':227.06, 'screen_height_mm':157.79,
                       'expected_width':2360, 'expected_height':1640}
        self.index = 1
        self.rev = 1
        self.ack_rev = -1
        self.browser = {}
        self.last_seen = 0.0

    def plan(self):
        p = [{'type':'black','name':'black'}, {'type':'white','name':'white'}]
        for axis in ('x','y'):
            for freq in self.config['freqs']:
                for phase in range(4):
                    p.append({'type':'fringe','axis':axis,'freq':freq,'phase':phase,
                              'name':f'{axis}_f{freq}_p{phase}'})
        return p

    def state(self):
        with self.lock:
            return {'index':self.index,'rev':self.rev,
                    'pattern':self.plan()[self.index], 'config':dict(self.config)}

    def heartbeat(self):
        with self.lock:
            self.last_seen=time.monotonic()

    def status(self):
        with self.lock:
            return {'index':self.index,'ack_index':self.index if self.ack_rev == self.rev else -1,
                    'rev':self.rev,'ack_rev':self.ack_rev, 'browser':self.browser,
                    'connected':time.monotonic()-self.last_seen < 4.0,
                    'plan':self.plan(),'config':dict(self.config)}

    def select(self, index):
        with self.lock:
            index=int(index)
            if not 0<=index<len(self.plan()):
                raise ValueError('图案索引超出范围')
            self.index=index
            self.rev+=1
            return self.state()

    def configure(self, info):
        with self.lock:
            newcfg=dict(self.config)
            if 'freqs' in info:
                freqs=sorted(set(int(x) for x in info['freqs']))
                if 1 not in freqs or 4 not in freqs or any(x not in (1,4,8,16,32) for x in freqs):
                    raise ValueError('周期必须包含1和4，可选8、16、32')
                newcfg['freqs']=freqs
            for name in ('brightness','contrast'):
                if name in info:
                    value=int(info[name])
                    if not 0<=value<=255:
                        raise ValueError(f'{name}应为0到255')
                    newcfg[name]=value
            if newcfg['brightness']+newcfg['contrast']>255 or newcfg['brightness']-newcfg['contrast']<0:
                raise ValueError('亮度±对比度必须在0—255内')
            for name in ('screen_width_mm','screen_height_mm'):
                if name in info:
                    val=float(info[name])
                    if not 10<=val<=1000: raise ValueError('屏幕尺寸不合理')
                    newcfg[name]=val
            self.config=newcfg
            self.index=min(self.index,len(self.plan())-1)
            self.rev+=1
            return self.status()

    def acknowledge(self, obj):
        with self.lock:
            self.last_seen=time.monotonic()
            self.browser=obj.get('browser',{})
            if int(obj.get('rev',-1))==self.rev and int(obj.get('index',-1))==self.index:
                self.ack_rev=self.rev
            return {'ok':True, 'ack_rev':self.ack_rev}

    def wait_ack(self, rev, timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            s=self.status()
            if s['ack_rev']==rev:
                b=s['browser']
                c=s['config']
                if (b.get('width') != c['expected_width'] or
                    b.get('height') != c['expected_height']):
                    raise RuntimeError(f'iPad画布尺寸不符：{b.get("width")}×{b.get("height")}，需要{c["expected_width"]}×{c["expected_height"]}；请保持全屏、横屏')
                return s
            time.sleep(.12)
        raise RuntimeError('等待iPad渲染确认超时。请在iPad上打开网页并保持亮屏。')

patterns=PatternController()
