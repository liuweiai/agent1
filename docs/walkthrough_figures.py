"""Code-native flow figures with shared SVG and high-resolution PNG geometry."""
from pathlib import Path
from html import escape
from math import atan2, cos, sin, pi
from PIL import Image, ImageDraw, ImageFont


class Figure:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.image = Image.new('RGB', (width, height), 'white')
        self.draw = ImageDraw.Draw(self.image)
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/>']
        self.boxes = {}

    def text(self, x, y, text, size=38, color='#203449', bold=False):
        font = ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc' if bold else 'C:/Windows/Fonts/msyh.ttc', size)
        # Pillow and SVG use the same center baseline anchor.
        self.draw.text((x, y), text, font=font, fill=color, anchor='mm')
        self.svg.append(f'<text x="{x}" y="{y}" font-family="Microsoft YaHei, sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}" text-anchor="middle" dominant-baseline="central">{escape(text)}</text>')

    def box(self, key, x, y, w, h, lines, fill='#F1F5F9', size=38):
        self.boxes[key] = (x, y, w, h)
        self.draw.rounded_rectangle((x,y,x+w,y+h), radius=16, fill=fill, outline='#486782', width=3)
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{fill}" stroke="#486782" stroke-width="3"/>')
        for i, line in enumerate(lines):
            self.text(x+w/2, y+h/2+(i-(len(lines)-1)/2)*(size+12), line, size, bold=i==0)

    def port(self, key, side):
        x,y,w,h = self.boxes[key]
        return {'top':(x+w/2,y),'bottom':(x+w/2,y+h),'left':(x,y+h/2),'right':(x+w,y+h/2)}[side]

    def line(self, pts, arrow=False, color='#486782', width=4):
        self.draw.line(pts, fill=color, width=width, joint='curve')
        self.svg.append('<polyline points="'+' '.join(f'{x},{y}' for x,y in pts)+f'" fill="none" stroke="{color}" stroke-width="{width}"/>')
        if arrow:
            (px,py),(x,y)=pts[-2:]
            angle=atan2(y-py,x-px)
            tri=[(x,y),(x-18*cos(angle-pi/6),y-18*sin(angle-pi/6)),(x-18*cos(angle+pi/6),y-18*sin(angle+pi/6))]
            self.draw.polygon(tri,fill=color)
            self.svg.append('<polygon points="'+' '.join(f'{a},{b}' for a,b in tri)+f'" fill="{color}"/>')

    def link(self,a,b,sa='bottom',sb='top',via=(),color='#486782'):
        self.line([self.port(a,sa),*via,self.port(b,sb)],True,color)

    def save(self,path):
        path=Path(path)
        self.image.save(path,dpi=(300,300))
        path.with_suffix('.svg').write_text('\n'.join(self.svg)+ '</svg>',encoding='utf-8')


def graph_figure(path):
    f=Figure(1800,2390)
    f.box('start',740,15,320,80,['START'],size=38)
    f.box('triage',580,135,640,115,['01 triage','异常分类和设备范围'],size=38)
    f.link('start','triage')
    xs=[240,680,1120,1560]
    f.line([(900,250),(900,300)])
    f.line([(xs[0],300),(xs[-1],300)])
    branches=[('mes','02 mes_data','设备和质量'),('alarms','03 alarm_data','设备告警'),('maint','04 maintenance_data','历史维修'),('knowledge','05 knowledge_data','手册和 SOP')]
    for x,(key,title,label) in zip(xs,branches):
        f.box(key,x-205,355,410,140,[title,label],fill='#EAF3F8',size=33)
        f.line([(x,300),(x,355)],True)
        f.line([(x,495),(x,550)])
    f.line([(240,550),(1560,550)])
    f.text(1360,593,'四路全部完成后汇合',32)
    f.box('stats',540,615,720,120,['06 statistical_analysis','计算统计证据'],fill='#EAF6F4',size=39)
    f.line([(900,550),(900,615)],True)
    f.line([(900,735),(900,775)])
    f.line([(500,775),(1300,775)])
    for x,key,lines in [(500,'neo',['07 graph_knowledge','Neo4j 固定路径查询']), (1300,'a2a',['08 vendor_expert','A2A 厂商服务意见'])]:
        f.box(key,x-320,825,640,130,lines,fill='#EAF3F8',size=38)
        f.line([(x,775),(x,825)],True)
        f.line([(x,955),(x,1005)])
    f.line([(500,1005),(1300,1005)])
    f.text(1390,1050,'两路完成后汇合',32)
    f.box('rca',570,1070,660,115,['09 rca','根因假设和证据 ID'],size=39)
    f.line([(900,1005),(900,1070)],True)
    f.box('plan',570,1230,660,115,['10 plan','生成两项固定处置计划'],size=39)
    f.link('rca','plan')
    f.box('approval',440,1390,920,135,['11 approval','interrupt 暂停   同一 thread_id 恢复'],fill='#FFF5DE',size=38)
    f.link('plan','approval')
    f.line([(900,1525),(900,1575)])
    f.line([(500,1575),(1300,1575)])
    f.text(300,1615,'Command 批准',32,color='#167D7A')
    f.text(1500,1615,'Command 拒绝',32,color='#A9342C')
    f.box('execute',180,1660,640,120,['12 execute','两个 Action MCP 写调用'],fill='#EAF6F4',size=38)
    f.box('reject',980,1660,640,120,['13 reject','不调用写工具'],fill='#FCECE9',size=38)
    f.line([(500,1575),(500,1660)],True,color='#167D7A')
    f.line([(1300,1575),(1300,1660)],True,color='#A9342C')
    f.box('verify',180,1840,640,125,['14 verify','DB 读回与模拟恢复判断'],size=38)
    f.link('execute','verify')
    f.line([(500,1965),(500,2030),(900,2030)])
    f.line([(1300,1780),(1300,2030),(900,2030)],color='#A9342C')
    f.box('report',530,2090,740,110,['15 report','组装事故报告'],size=39)
    f.line([(900,2030),(900,2090)],True)
    f.box('end',740,2250,320,80,['END'],size=38)
    f.link('report','end')
    f.text(900,2370,'节点编号仅用于阅读顺序   当前图没有 verify 返回 plan 的循环',30)
    f.save(path)


def mcp_figure(path):
    f=Figure(1800,1750)
    boxes=[
        ('node',80,['collect_mes_data','self.mes.get_recent_quality(run_id, line_id, 20)']),
        ('adapter',315,['RemoteMesTools.get_recent_quality','caller.call("get_recent_quality", arguments)']),
        ('caller',550,['MCPRemoteCaller','Streamable HTTP → client.call_tool(...)']),
        ('server',820,['mes_server.get_recent_quality','@mcp.tool() 注册的服务端函数']),
        ('impl',1055,['MesReadTools.get_recent_quality','参数校验 → 固定 SELECT → finally 审计']),
        ('db',1290,['MySQL 或 SQLite','batches + inspections + process_metrics'])]
    for key,y,lines in boxes:
        f.box(key,210,y,1380,155,lines,size=40,fill='#EAF3F8' if key in {'adapter','caller','server'} else '#F1F5F9')
    for (a,_,_),(b,_,_) in zip(boxes,boxes[1:]):f.link(a,b)
    f.text(1140,767,'HTTP 进程边界',34,color='#167D7A')
    f.text(900,1540,'结果沿原路径返回 → quality → 统计分析 → RCA',42,bold=True)
    f.text(900,1630,'inprocess 模式：Node 直接调用 MesReadTools，跳过中间三层',37)
    f.text(900,1700,'日志中的 mes-mcp 标签本身不能证明发生了网络调用',34,color='#5E6A73')
    f.save(path)
