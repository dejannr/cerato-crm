"""Sanitize rich email fragments and attach embedded images as MIME content IDs."""
import base64
import binascii
import html
import io
import re
from email.utils import make_msgid
from html.parser import HTMLParser
from urllib.parse import urlsplit
import nh3
from PIL import Image, UnidentifiedImageError

MAX_HTML = 180000
MAX_IMAGE_TOTAL = 60000
DATA_IMAGE = re.compile(r'^data:image/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/]*={0,2})$', re.I)
TAGS={'p','br','strong','b','em','i','u','s','ul','ol','li','a','blockquote','img','span','mark','hr','table','tbody','thead','tfoot','tr','td','th'}
ATTRIBUTES={'a':{'href','title'},'img':{'src','alt','title','style'},'span':{'style'},'mark':{'style'},'p':{'style'},'table':{'style'},'td':{'style','colspan','rowspan'},'th':{'style','colspan','rowspan'}}

def clean_style(value):
    """Keep only small, presentation-only CSS values needed by the editor/signature."""
    allowed={
        'color': re.compile(r'^#[0-9a-f]{3,8}$',re.I),
        'background-color': re.compile(r'^#[0-9a-f]{3,8}$',re.I),
        'font-size': re.compile(r'^(?:10|11|12|14|16|18|20|24|28|32|36)px$'),
        'font-family': re.compile(r'^(?:Arial|Georgia|Tahoma|Verdana|Courier New|sans-serif|serif)$',re.I),
        'text-align': re.compile(r'^(?:left|center|right|justify)$',re.I),
        'vertical-align': re.compile(r'^(?:top|middle|bottom)$',re.I),
        'border-left': re.compile(r'^1px solid #[0-9a-f]{3,8}$',re.I),
        'padding': re.compile(r'^(?:0 14px|0 12px|8px|10px|12px)$',re.I),
    }
    declarations=[]
    for declaration in value.split(';'):
        name,sep,raw=declaration.partition(':')
        name=name.strip().lower(); raw=raw.strip()
        if sep and name in allowed and allowed[name].fullmatch(raw): declarations.append(f'{name}: {raw}')
    return '; '.join(declarations) or None

def plain_to_html(value):
    return '<p>'+html.escape(value or '').replace('\n','<br>')+'</p>'

def clean_fragment(value):
    if len(value)>MAX_HTML: raise ValueError('Email content is too large.')
    images=[]
    errors=[]
    def attribute(tag,name,value):
        if name=='style': return clean_style(value)
        if tag in ('td','th') and name in ('colspan','rowspan'):
            return value if value.isdigit() and 1<=int(value)<=8 else None
        if tag=='a' and name=='href':
            try:
                parsed=urlsplit(value)
                return value if parsed.scheme.lower() in ('http','https','mailto') else None
            except ValueError: return None
        if tag=='img' and name=='src':
            match=DATA_IMAGE.fullmatch(value)
            if not match: return None
            try:
                blob=base64.b64decode(match.group(2),validate=True)
                img=Image.open(io.BytesIO(blob))
                if img.width*img.height>8000000: errors.append('Image dimensions are too large.'); return None
                img.verify()
                expected={'png':'PNG','jpeg':'JPEG','gif':'GIF','webp':'WEBP'}[match.group(1).lower()]
                if img.format!=expected: errors.append('Image type does not match its contents.'); return None
            except (binascii.Error,UnidentifiedImageError,OSError,Image.DecompressionBombError): errors.append('Invalid image.'); return None
            images.append(blob)
            if sum(map(len,images))>MAX_IMAGE_TOTAL: errors.append('Images must total 60 KB or less.'); return None
            return value
        return value
    result=nh3.clean(value,tags=TAGS,attributes=ATTRIBUTES,attribute_filter=attribute,clean_content_tags={'script','style','iframe','object','svg','math'},url_schemes={'https','http','mailto','data'},url_relative='deny',link_rel='noopener noreferrer')
    if errors: raise ValueError(errors[0])
    return result

class TextVersion(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('br','p','li','blockquote','tr'): self.parts.append('\n')
        if tag=='td' and self.parts and not self.parts[-1].endswith(('\n','\t')): self.parts.append('\t')
        if tag=='li': self.parts.append('• ')
        if tag=='a': self.links.append(dict(attrs).get('href',''))
        if tag=='img':
            alt=dict(attrs).get('alt','')
            if alt: self.parts.append('['+alt+']')
    def handle_endtag(self,tag):
        if tag=='a' and self.links:
            target=self.links.pop()
            if target: self.parts.append(' ('+target+')')
        if tag in ('p','li','blockquote','tr'): self.parts.append('\n')
    def handle_data(self,data): self.parts.append(data)

def plain_text(value):
    parser=TextVersion();parser.feed(value)
    return re.sub(r'\n{3,}','\n\n',''.join(parser.parts)).strip()

def mime_html(value):
    """Replace sanitized embedded data images with Content-ID references."""
    images=[]
    def replace(match):
        data=DATA_IMAGE.fullmatch(html.unescape(match.group(1)))
        if not data: return match.group(0)
        cid=make_msgid(domain='ceratosystems.com'); images.append((cid,data.group(1).lower(),base64.b64decode(data.group(2))))
        return 'src="cid:'+cid[1:-1]+'"'
    result=re.sub(r'src="([^"]+)"',replace,value)
    return result,images
