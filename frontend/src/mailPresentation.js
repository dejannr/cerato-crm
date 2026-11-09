// Split common email quoting conventions without discarding message content.
export function splitQuotedMessage(value){
 const text=String(value||'').replace(/\r\n?/g,'\n');
 const lines=text.split('\n');
 for(let i=1;i<lines.length;i++){
  if(!lines.slice(0,i).some(line=>line.trim()))continue;
  const line=lines[i].trim();
  const separator=/^On .{1,400}wrote:\s*$/i.test(line)||/^-{2,}\s*Original Message\s*-{2,}$/i.test(line)||/^_{5,}$/.test(line);
  const outlook=/^From:\s*\S/i.test(line)&&lines.slice(i+1,i+6).some(next=>/^(Sent|Date|To|Subject):\s*/i.test(next.trim()));
  const quoted=/^>/.test(line)&&lines.slice(i).filter(next=>/^\s*>/.test(next)).length>=2;
  if(separator||outlook||quoted)return {message:lines.slice(0,i).join('\n').trimEnd(),quoted:lines.slice(i).join('\n')};
 }
 return {message:text,quoted:''};
}
export function isReply(message){return Boolean(message?.in_reply_to||message?.references||/^\s*re\s*:/i.test(message?.subject||''));}
export function displayName(value){const raw=String(value||'').trim();if(!raw)return 'Unknown sender';const match=raw.match(/^\s*"?([^"<]+?)"?\s*<[^>]+>/);return match?match[1].trim():raw;}
