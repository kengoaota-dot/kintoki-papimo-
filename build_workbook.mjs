// Work-side numeric workbook adapter. Run with CODEX_PRIMARY_RUNTIME_NODE.
// Existing cells, formulas, sheets, and historical rows are not cleared.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { FileBlob, SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const args=process.argv.slice(2);
const flag=(name)=>args.includes(name)?args[args.indexOf(name)+1]:null;
const inputPath=flag('--input'), outputPath=flag('--output'), template=flag('--template');
if(!inputPath || !outputPath)throw new Error('--input excel_input.json --output absolute.xlsx required');
if(template && !flag('--map'))throw new Error('Existing canonical workbook requires an explicit column map; refusing duplicate English columns');
const data=JSON.parse(await fs.readFile(inputPath,'utf8'));
const maps=flag('--map')?JSON.parse(await fs.readFile(flag('--map'),'utf8')):{};
const wb=template?await SpreadsheetFile.importXlsx(await FileBlob.load(template)):Workbook.create();
const dir=path.dirname(outputPath);
await fs.mkdir(dir,{recursive:true});
if(template){
  const before=await wb.render({sheetName:wb.worksheets.getItemAt(0).name,range:'A1:H12',scale:1,format:'png'});
  await fs.writeFile(path.join(dir,'before-workbook.png'),new Uint8Array(await before.arrayBuffer()));
}
const stats=[];
for(const [sheetName,rows] of Object.entries(data.sheets)){
  if(!rows.length)continue;
  let sheet,isNew=false;
  try{sheet=wb.worksheets.getItem(sheetName);}catch{sheet=wb.worksheets.add(sheetName);isNew=true;}
  const map=maps[sheetName]||{};
  const fields=[...new Set(rows.flatMap(r=>Object.keys(r)))];
  const targetName=(key)=>map[key]||key;
  const used=sheet.getUsedRange();
  const values=used?.values||[];
  let headers=values.length?values[0].map(v=>v==null?'':String(v)):[];
  if(headers.length && sheet.getRange('A1').values[0][0]!==values[0][0])throw new Error('Header must start at A1; supply a dedicated mapped target sheet');
  const missing=fields.map(targetName).filter(h=>!headers.includes(h));
  if(sheet.tables.items.length && missing.length)throw new Error('Existing native table requires an explicit column mapping; no table or formula is rewritten');
  headers=[...headers,...missing];
  if(missing.length)sheet.getRangeByIndexes(0,headers.length-missing.length,1,missing.length).values=[missing];
  const identity=(r)=>JSON.stringify(['snapshot_id','business_date','machine','row','item'].map(k=>String(r[k]??'')));
  const oldKeys=new Set(values.slice(1).map(row=>{
    const o={};for(const k of ['snapshot_id','business_date','machine','row','item','missing_items'])o[k]=row[headers.indexOf(targetName(k))];return identity(o);
  }));
  const additions=[];
  for(const row of rows){
    const key=identity(row);
    if(oldKeys.has(key))continue;
    oldKeys.add(key);
    additions.push(headers.map(h=>{
      const k=fields.find(k=>targetName(k)===h);const v=k?row[k]:null;
      if(v==null)return null;
      if(typeof v==='object')return JSON.stringify(v);
      return typeof v==='string' && v.startsWith('=')?"'"+v:v;
    }));
  }
  const start=Math.max(1,values.length);
  if(additions.length){
    if(sheet.tables.items.length===1)sheet.tables.items[0].rows.add(null,additions);
    else sheet.getRangeByIndexes(start,0,additions.length,headers.length).values=additions;
    for(const key of ['signed_difference','highest_difference','lowest_difference']){
      const column=headers.indexOf(targetName(key));
      if(column>=0)sheet.getRangeByIndexes(start,column,additions.length,1).setNumberFormat('+#,##0;-#,##0;0');
    }
  }
  if(isNew){
    sheet.showGridLines=false;
    sheet.getRangeByIndexes(0,0,1,headers.length).format={fill:'#22344D',horizontalAlignment:'center',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'}};
    sheet.getRangeByIndexes(0,0,Math.max(2,start+additions.length),headers.length).format.columnWidth=20;
    sheet.freezePanes.freezeRows(1);
    sheet.freezePanes.freezeColumns(3);
  }
  stats.push({sheet:sheetName,appended:additions.length,existingRows:Math.max(0,values.length-1)});
}
wb.recalculate();
const check=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!',options:{useRegex:true,maxResults:30},maxChars:2000});
console.log(check.ndjson);
const xlsx=await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(outputPath);
for(const entry of stats){
  const preview=await wb.render({sheetName:entry.sheet,range:'A1:H12',scale:1,format:'png'});
  await fs.writeFile(path.join(dir,'preview-'+entry.sheet+'.png'),new Uint8Array(await preview.arrayBuffer()));
}
const bytes=await fs.readFile(outputPath);
await fs.writeFile(path.join(dir,'workbook_export_receipt.json'),JSON.stringify({business_date:data.business_date,
  snapshot_ids:data.snapshot_ids,workbook_sha256:crypto.createHash('sha256').update(bytes).digest('hex'),
  writeback_verified:false,note:'Export only. Mark writeback verified only after replacing the canonical Library workbook and checking its returned version.',stats},null,2));
console.log(JSON.stringify({output:outputPath,stats}));
