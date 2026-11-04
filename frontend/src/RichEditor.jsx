import { ReverseLeft, ReverseRight, Bold01, Italic01, Underline01, Strikethrough01, AlignLeft, AlignCenter, AlignRight, List, LeftIndent01, RightIndent01, Link01, Image01, Pilcrow01, Minus, Table as TableIcon, Eraser, Type01, Paint } from '@untitledui/icons';
import React,{useEffect,useRef,useState} from 'react';
import {useEditor,EditorContent} from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Image from '@tiptap/extension-image';
import Underline from '@tiptap/extension-underline';
import Link from '@tiptap/extension-link';
import TextAlign from '@tiptap/extension-text-align';
import {TextStyle,FontSize} from '@tiptap/extension-text-style';
import Color from '@tiptap/extension-color';
import FontFamily from '@tiptap/extension-font-family';
import Highlight from '@tiptap/extension-highlight';
import {Table,TableRow,TableCell,TableHeader} from '@tiptap/extension-table';
import stefanPhoto from './assets/stefan-signature.jpg?inline';

const StyledCell=TableCell.extend({
 addAttributes(){return {...this.parent?.(),style:{default:null,parseHTML:el=>el.getAttribute('style'),renderHTML:attrs=>attrs.style?{style:attrs.style}:{}}}},
});
const EDITOR_EXTENSIONS=[StarterKit.configure({heading:false,codeBlock:false,link:false}),TextStyle,FontSize,Color,FontFamily,Underline,Link.configure({openOnClick:false,autolink:true,HTMLAttributes:{rel:'noopener noreferrer',target:'_blank'}}),Image.configure({inline:true,allowBase64:true}),TextAlign.configure({types:['paragraph','listItem']}),Highlight.configure({multicolor:true}),Table.configure({resizable:false}),TableRow,StyledCell,TableHeader];

export default function RichEditor({initialHtml,onChange,disabled=false,label='Message',signaturePreset}) {
 const input=useRef(null),[warning,setWarning]=useState('');
 const editor=useEditor({extensions:EDITOR_EXTENSIONS,content:initialHtml||'<p></p>',editable:!disabled,onUpdate:({editor})=>onChange({html:editor.getHTML(),text:editor.getText({blockSeparator:'\n\n'})}),editorProps:{attributes:{'aria-label':label,'role':'textbox','aria-multiline':'true'}}});
 useEffect(()=>{editor?.setEditable(!disabled)},[editor,disabled]);
 function signatureHtml(preset){const name=preset.name,role=preset.role,email=preset.email;const photo=preset.photo?`<td style="border-left:1px solid #d7e1da;padding:0 14px;vertical-align:middle"><img src="${stefanPhoto}" alt="${name}" /></td>`:'';return `<table><tbody><tr><td><p>Best,</p><p><strong>${name}</strong><br>${role}, Cerato Systems<br><a href="mailto:${email}">${email}</a></p></td>${photo}</tr></tbody></table>`}
 useEffect(()=>{if(!editor||!signaturePreset)return;const text=editor.getText({blockSeparator:'\n'}).replace(/\s+/g,'').toLowerCase();const legacy=`Best,${signaturePreset.name}${signaturePreset.role},CeratoSystems`.replace(/\s+/g,'').toLowerCase();if(text===legacy)editor.commands.setContent(signatureHtml(signaturePreset),{emitUpdate:true})},[editor,signaturePreset]);
 const button=(title,text,action,active=false)=><button type="button" title={title} aria-label={title} aria-pressed={active} className={active?'active':''} onMouseDown={e=>e.preventDefault()} onClick={action} disabled={disabled}>{text}</button>;
 function addLink(){const value=window.prompt('Enter an https://, http:// or mailto: link');if(value===null)return;if(!/^(https?:\/\/|mailto:)[^\s]+$/i.test(value)){setWarning('Enter a valid https://, http:// or mailto: link.');return}setWarning('');editor.chain().focus().extendMarkRange('link').setLink({href:value}).run()}
 function imageChanged(e){const file=e.target.files?.[0];e.target.value='';if(!file)return;if(!['image/png','image/jpeg','image/gif','image/webp'].includes(file.type)){setWarning('Choose a PNG, JPEG, GIF or WebP image.');return}if(file.size>50000){setWarning('Choose an image smaller than 50 KB. All images in one email must total 60 KB or less.');return}const reader=new FileReader();reader.onload=()=>{editor.chain().focus().setImage({src:reader.result,alt:file.name}).run();setWarning('')};reader.onerror=()=>setWarning('Could not read this image.');reader.readAsDataURL(file)}
 function insertSignature(){if(!signaturePreset)return;editor.commands.setContent(signatureHtml(signaturePreset),{emitUpdate:true});setWarning('');}
 function colorChanged(e){const value=e.target.value;editor.chain().focus().setColor(value).run()}
 function highlightChanged(e){const value=e.target.value;editor.chain().focus().toggleHighlight({color:value}).run()}
 if(!editor)return null;
 return <div className={'rich-editor '+(disabled?'disabled':'')}><div className="rich-toolbar" role="toolbar" aria-label={label+' formatting'}>
  {button('Undo',<ReverseLeft className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().undo().run())}{button('Redo',<ReverseRight className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().redo().run())}<span className="rich-divider"/>
  <select aria-label="Font family" title="Font family" defaultValue="" disabled={disabled} onChange={e=>{if(e.target.value)editor.chain().focus().setFontFamily(e.target.value).run();e.target.value=''}}><option value="">Font</option><option value="Arial">Arial</option><option value="Georgia">Georgia</option><option value="Tahoma">Tahoma</option><option value="Verdana">Verdana</option><option value="Courier New">Courier New</option></select>
  <select aria-label="Font size" title="Font size" defaultValue="" disabled={disabled} onChange={e=>{if(e.target.value)editor.chain().focus().setMark('textStyle',{fontSize:e.target.value}).run();e.target.value=''}}><option value="">Size</option><option value="12px">Small</option><option value="14px">Normal</option><option value="18px">Large</option><option value="24px">Huge</option></select>
  {button('Bold',<Bold01 className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().toggleBold().run(),editor.isActive('bold'))}{button('Italic',<Italic01 className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().toggleItalic().run(),editor.isActive('italic'))}{button('Underline',<Underline01 className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().toggleUnderline().run(),editor.isActive('underline'))}{button('Strikethrough',<Strikethrough01 className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().toggleStrike().run(),editor.isActive('strike'))}
  <label className="rich-color" title="Text color"><Type01 className="ui-icon" aria-hidden="true"/><input aria-label="Text color" type="color" defaultValue="#26362e" disabled={disabled} onChange={colorChanged}/></label><label className="rich-color" title="Highlight color"><Paint className="ui-icon" aria-hidden="true"/><input aria-label="Highlight color" type="color" defaultValue="#fff2a8" disabled={disabled} onChange={highlightChanged}/></label><span className="rich-divider"/>
  {button('Align left',<AlignLeft className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().setTextAlign('left').run())}{button('Align center',<AlignCenter className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().setTextAlign('center').run())}{button('Align right',<AlignRight className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().setTextAlign('right').run())}
  {button('Bulleted list',<List className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().toggleBulletList().run(),editor.isActive('bulletList'))}{button('Numbered list',<><span className="list-number">1.</span><List className="ui-icon" aria-hidden="true"/></>,()=>editor.chain().focus().toggleOrderedList().run(),editor.isActive('orderedList'))}{button('Decrease indent',<LeftIndent01 className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().liftListItem('listItem').run())}{button('Increase indent',<RightIndent01 className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().sinkListItem('listItem').run())}
  {button('Insert link',<Link01 className="ui-icon" aria-hidden="true"/>,addLink,editor.isActive('link'))}{button('Insert image',<Image01 className="ui-icon" aria-hidden="true"/>,()=>input.current?.click())}<input ref={input} type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden onChange={imageChanged}/>
  {button('Quote',<Pilcrow01 className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().toggleBlockquote().run(),editor.isActive('blockquote'))}{button('Insert divider',<Minus className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().setHorizontalRule().run())}{button('Insert table',<TableIcon className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().insertTable({rows:2,cols:2,withHeaderRow:false}).run())}{button('Clear formatting',<Eraser className="ui-icon" aria-hidden="true"/>,()=>editor.chain().focus().clearNodes().unsetAllMarks().run())}
  {signaturePreset&&<>{button('Insert professional signature','Signature',insertSignature)}{signaturePreset.photo&&<small className="signature-image-note">Photo included</small>}</>}
 </div><EditorContent editor={editor}/>{warning&&<p className="rich-warning" role="alert">{warning}</p>}</div>
}
