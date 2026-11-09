import test from 'node:test';
import assert from 'node:assert/strict';
import {splitQuotedMessage,isReply,displayName} from '../src/mailPresentation.js';

test('separates a reply from quoted earlier text without losing it',()=>{
 const text='Thanks, that works.\n\nOn Monday, Alex wrote:\n> Original message\n> More details';
 const result=splitQuotedMessage(text);
 assert.equal(result.message,'Thanks, that works.');
 assert.equal(result.quoted,'On Monday, Alex wrote:\n> Original message\n> More details');
});
test('recognizes Outlook headers and does not hide original messages',()=>{
 const result=splitQuotedMessage('My reply\n\nFrom: Alex <alex@example.com>\nSent: Monday\nSubject: Hello\n\nOriginal');
 assert.equal(result.message,'My reply');assert.match(result.quoted,/Subject: Hello/);
 assert.deepEqual(splitQuotedMessage('> An entirely quoted message\n> stays visible'),{message:'> An entirely quoted message\n> stays visible',quoted:''});
 assert.deepEqual(splitQuotedMessage('A message without a quote.'),{message:'A message without a quote.',quoted:''});
});
test('labels replies from headers or subject and displays sender safely as text',()=>{
 assert.equal(isReply({in_reply_to:'<x>'}),true);assert.equal(isReply({subject:'Re: Hello'}),true);assert.equal(isReply({subject:'Hello'}),false);
 assert.equal(displayName('"Alex Smith" <alex@example.com>'),'Alex Smith');assert.equal(displayName('alex@example.com'),'alex@example.com');
});
