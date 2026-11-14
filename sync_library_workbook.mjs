// Work-side adapter only. Existing Library files never go into the public repository.
// Run with CODEX_PRIMARY_RUNTIME_NODE --max-old-space-size=8192.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';
const a=process.argv.slice(2);const arg=k=>a[a.indexOf(k)+1];
const template=arg('--template'),patchPath=arg('--patch'),output=arg('--output');
if(!a.includes('--template')||!a.includes('--patch')||!a.includes('--output'))throw Error('template, patch and output required');
const patch=JSON.parse(await fs.readFile(patchPath,'utf8'));
const outputDir=path.dirname(output);await fs.mkdir(outputDir,{recursive:true});
if(patch.sheets.every(s=>s.values.length===0)){
  await fs.copyFile(template,output);console.log(JSON.stringify({unchanged:true}));process.exit(0);
}
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(template));
const stats=[];
const prepared=new Map();
const excelJst=v=>{
  if(v==null||v==='')return null;
  const s=String(v).replaceAll('/','-').replace(' ','T');
  const wall=s.replace(/(?:\+09:00|Z)$/,'');
  const d=new Date(wall+'Z');
  if(Number.isNaN(d.getTime()))throw Error('Invalid JST timestamp: '+v);
  return d;
};
for(const batch of patch.sheets){
  if(!batch.values.length)continue;
  const sheet=wb.worksheets.getItem(batch.sheet);
  const headers=sheet.getRangeByIndexes(0,0,1,batch.headers.length).values[0];
  if(JSON.stringify(headers)!==JSON.stringify(batch.headers))throw Error('Header mismatch: '+batch.sheet);
  if(sheet.tables.items.length)throw Error('Existing table requires explicit extension support: '+batch.sheet);
  const current=sheet.getUsedRange().values.length;
  if(current!==batch.start_row)throw Error('Source workbook changed; refresh with version guard: '+batch.sheet);
  const preview=await wb.render({sheetName:batch.sheet,range:`A${Math.max(1,current-6)}:H${current}`,scale:1,format:'png'});
  await fs.writeFile(path.join(outputDir,`before-${batch.sheet}.png`),new Uint8Array(await preview.arrayBuffer()));
  // Extend the immediate row's formatting, then write actual source values in one block.
  for(let i=0;i<batch.values.length;i++){
    sheet.getRangeByIndexes(current+i,0,1,batch.headers.length).copyFrom(sheet.getRangeByIndexes(current-1,0,1,batch.headers.length),'all');
  }
  const values=batch.values.map(row=>row.map((v,j)=>String(batch.headers[j]).includes('JST')?excelJst(v):typeof v==='string'&&v.startsWith('=')?"'"+v:v));
  prepared.set(batch.sheet,values);
  sheet.getRangeByIndexes(current,0,values.length,batch.headers.length).values=values;
  batch.headers.forEach((h,j)=>{if(String(h).includes('JST'))sheet.getRangeByIndexes(current,j,values.length,1).setNumberFormat('yyyy-mm-dd hh:mm:ss');});
  for(const col of batch.numeric_columns)sheet.getRangeByIndexes(current,col,values.length,1).setNumberFormat('+#,##0;-#,##0;0');
  stats.push({sheet:batch.sheet,existingRows:current-1,appended:values.length,startRow:current+1});
}
wb.recalculate();
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!',options:{useRegex:true,maxResults:30},maxChars:1500});
console.log(errors.ndjson);
for(const batch of patch.sheets){
  if(!batch.values.length)continue;
  const check=wb.worksheets.getItem(batch.sheet).getRangeByIndexes(batch.start_row,0,batch.values.length,batch.headers.length).values;
  const norm=v=>v==null||v===''?null:v instanceof Date?v.getTime():v;
  const expectedValues=prepared.get(batch.sheet);
  for(let i=0;i<batch.values.length;i++)for(let j=0;j<batch.headers.length;j++){
    // Excel serial dates can lose one millisecond in a numeric round trip.
    // Original sub-second JST strings remain exact in the CSV/JSON ledger.
    if(check[i]?.[j] instanceof Date&&expectedValues[i][j] instanceof Date&&Math.abs(check[i][j].getTime()-expectedValues[i][j].getTime())<=2)continue;
    if(norm(check[i]?.[j])!==norm(expectedValues[i][j])){
      throw Error('Value verification mismatch: '+JSON.stringify({sheet:batch.sheet,row:i,column:batch.headers[j],actual:check[i]?.[j],expected:expectedValues[i][j]}));
    }
  }
  const preview=await wb.render({sheetName:batch.sheet,range:`A${batch.start_row+1}:H${batch.start_row+7}`,scale:1,format:'png'});
  await fs.writeFile(path.join(outputDir,`after-${batch.sheet}.png`),new Uint8Array(await preview.arrayBuffer()));
}
const result=await SpreadsheetFile.exportXlsx(wb);await result.save(output);
const sha=crypto.createHash('sha256').update(await fs.readFile(output)).digest('hex');
await fs.writeFile(path.join(outputDir,'workbook_sync_receipt.json'),JSON.stringify({sync_id:patch.sync_id,sha256:sha,stats,library_writeback_verified:false},null,2));
console.log(JSON.stringify({output,sha256:sha,stats}));
