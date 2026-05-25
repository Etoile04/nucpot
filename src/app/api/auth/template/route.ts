import { NextRequest, NextResponse } from 'next/server'
import { supabase } from '@/lib/supabase'

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const lang = searchParams.get('lang') || 'zh'
  const autoPrint = searchParams.get('print') === '1'

  // Potential info from form
  const potential = {
    name: searchParams.get('name') || '',
    type: searchParams.get('type') || '',
    elements: searchParams.get('elements') || '',
    systemName: searchParams.get('systemName') || '',
    doiRefs: searchParams.get('doiRefs') || '',
  }

  // User info from query params (fallback)
  const queryUser = {
    name: searchParams.get('userName') || '',
    email: searchParams.get('userEmail') || '',
  }

  // Try to fetch profile from DB for richer info
  const userId = searchParams.get('userId')
  let profileUser = {
    name: queryUser.name,
    email: queryUser.email,
    affiliation: '',
    title: '',
    phone: '',
  }

  if (userId) {
    try {
      const { data: profile } = await supabase
        .from('profiles')
        .select('full_name, email, affiliation, title, phone')
        .eq('id', userId)
        .single()
      if (profile) {
        profileUser = {
          name: profile.full_name || queryUser.name,
          email: profile.email || queryUser.email,
          affiliation: profile.affiliation || '',
          title: profile.title || '',
          phone: profile.phone || '',
        }
      }
    } catch {
      // Fallback to query params
    }
  }

  const today = new Date().toISOString().split('T')[0]

  if (lang === 'en') {
    return NextResponse.json(
      { html: generateEnglish(potential, profileUser, today, autoPrint) },
      { headers: { 'Content-Type': 'application/json' } }
    )
  }

  return NextResponse.json(
    { html: generateChinese(potential, profileUser, today, autoPrint) },
    { headers: { 'Content-Type': 'application/json' } }
  )
}

function esc(s: string) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function generateChinese(p: { name: string; type: string; elements: string; systemName: string; doiRefs: string }, u: { name: string; email: string; affiliation: string; title: string; phone: string }, today: string, autoPrint: boolean) {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>势函数分发授权书 - NucPot</title>
<style>
  @page { size: A4; margin: 25mm 20mm; }
  * { box-sizing: border-box; }
  body { font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif; color: #1a1a1a; line-height: 1.7; max-width: 210mm; margin: 0 auto; padding: 20px 15px; }
  h1 { text-align: center; font-size: 22px; margin-bottom: 5px; }
  .subtitle { text-align: center; color: #666; font-size: 13px; margin-bottom: 25px; }
  .note { background: #f0f7ff; border-left: 4px solid #2563eb; padding: 10px 15px; margin: 20px 0; font-size: 13px; border-radius: 0 6px 6px 0; }
  table { width: 100%; border-collapse: collapse; margin: 12px 0; }
  th, td { border: 1px solid #ccc; padding: 8px 12px; text-align: left; font-size: 14px; }
  th { background: #f5f5f5; width: 120px; white-space: nowrap; }
  td { min-width: 200px; }
  .section-title { font-size: 15px; font-weight: 600; margin-top: 20px; margin-bottom: 8px; padding-bottom: 4px; border-bottom: 2px solid #2563eb; color: #1a4080; }
  ol { padding-left: 20px; font-size: 14px; }
  ol li { margin-bottom: 6px; }
  .sig-area { margin-top: 40px; text-align: right; }
  .sig-line { display: inline-block; width: 250px; border-bottom: 1px solid #999; margin-left: 10px; }
  .sig-row { margin-bottom: 15px; font-size: 14px; }
  .footer { margin-top: 40px; padding-top: 12px; border-top: 1px solid #ddd; font-size: 11px; color: #999; }
  .checkbox { font-size: 14px; margin: 5px 0; }
  .checkbox input { margin-right: 5px; }
  .auto-filled { background: #f0fdf4; }
  @media print { body { padding: 0; } .no-print { display: none; } }
</style>
</head>
<body${autoPrint ? ' onload="window.print()"' : ''}>

<h1>势函数分发授权书</h1>
<div class="subtitle">NucPot — 核材料势函数开放平台</div>

<div class="note">
  本授权书用于确认势函数作者或权利人同意在 NucPot 平台上公开展示和分发其势函数。请确认信息无误后<strong>打印、签字、扫描</strong>，在上传势函数时一并提交。
</div>

<div class="section-title">一、势函数信息</div>
<table>
  <tr><th>势函数名称</th><td><strong>${esc(p.name)}</strong></td></tr>
  <tr><th>势函数类型</th><td>${esc(p.type)}</td></tr>
  <tr><th>涉及元素</th><td>${esc(p.elements)}</td></tr>
  <tr><th>体系名称</th><td>${esc(p.systemName)}</td></tr>
  <tr><th>关联论文 DOI</th><td>${esc(p.doiRefs) || '（如有）'}</td></tr>
</table>

<div class="section-title">二、授权内容</div>
<ol>
  <li>授权人同意 NucPot 平台以<strong>非商业、学术研究</strong>为目的，公开展示和提供该势函数的下载服务。</li>
  <li>平台用户可<strong>免费获取</strong>该势函数用于学术研究和教学活动。</li>
  <li>平台将在势函数页面<strong>标注原作者信息</strong>和引用文献。</li>
  <li>本授权<strong>不意味着权利转移</strong>，授权人保留对该势函数的全部知识产权。</li>
</ol>

<div class="section-title">三、授权人声明</div>
<div class="checkbox"><input type="checkbox" id="c1"><label for="c1">本人确认是该势函数的作者或已获得合法授权</label></div>
<div class="checkbox"><input type="checkbox" id="c2"><label for="c2">该势函数不侵犯任何第三方的知识产权</label></div>
<div class="checkbox"><input type="checkbox" id="c3"><label for="c3">本人有权作出本授权</label></div>

<div class="section-title">四、授权期限</div>
<p style="font-size:14px;">本授权自签署之日起生效，至授权人书面撤回为止。</p>

<div class="section-title">五、授权人信息</div>
<table>
  <tr><th>姓名</th><td class="auto-filled"><strong>${esc(u.name)}</strong></td></tr>
  <tr><th>单位</th><td class="auto-filled">${esc(u.affiliation) || ''}</td></tr>
  <tr><th>职务/职称</th><td class="auto-filled">${esc(u.title) || ''}</td></tr>
  <tr><th>电子邮箱</th><td class="auto-filled">${esc(u.email)}</td></tr>
  <tr><th>联系电话</th><td class="auto-filled">${esc(u.phone) || ''}</td></tr>
  <tr><th>日期</th><td>${today}</td></tr>
</table>

<div class="sig-area">
  <div class="sig-row">授权人签名：<span class="sig-line"></span></div>
  <div class="sig-row">日期：<span class="sig-line"></span></div>
  <div style="margin-top:10px; font-size:12px; color:#888;">（如需加盖单位公章，请在签名处同时盖章）</div>
</div>

<div class="footer">
  NucPot 平台保留审核和拒绝的权利。如有疑问请联系平台管理员。<br>
  本授权书最终解释权归 NucPot 平台所有。文档生成日期：${today}
</div>

</body>
</html>`
}

function generateEnglish(p: { name: string; type: string; elements: string; systemName: string; doiRefs: string }, u: { name: string; email: string; affiliation: string; title: string; phone: string }, today: string, autoPrint: boolean) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Interatomic Potential Distribution Authorization - NucPot</title>
<style>
  @page { size: A4; margin: 25mm 20mm; }
  * { box-sizing: border-box; }
  body { font-family: "Georgia", "Times New Roman", serif; color: #1a1a1a; line-height: 1.7; max-width: 210mm; margin: 0 auto; padding: 20px 15px; }
  h1 { text-align: center; font-size: 22px; margin-bottom: 5px; }
  .subtitle { text-align: center; color: #666; font-size: 13px; margin-bottom: 25px; }
  .note { background: #f0f7ff; border-left: 4px solid #2563eb; padding: 10px 15px; margin: 20px 0; font-size: 13px; border-radius: 0 6px 6px 0; }
  table { width: 100%; border-collapse: collapse; margin: 12px 0; }
  th, td { border: 1px solid #ccc; padding: 8px 12px; text-align: left; font-size: 14px; }
  th { background: #f5f5f5; width: 180px; white-space: nowrap; }
  td { min-width: 200px; }
  .section-title { font-size: 15px; font-weight: 600; margin-top: 20px; margin-bottom: 8px; padding-bottom: 4px; border-bottom: 2px solid #2563eb; color: #1a4080; }
  ol { padding-left: 20px; font-size: 14px; }
  ol li { margin-bottom: 6px; }
  .sig-area { margin-top: 40px; text-align: right; }
  .sig-line { display: inline-block; width: 250px; border-bottom: 1px solid #999; margin-left: 10px; }
  .sig-row { margin-bottom: 15px; font-size: 14px; }
  .footer { margin-top: 40px; padding-top: 12px; border-top: 1px solid #ddd; font-size: 11px; color: #999; }
  .checkbox { font-size: 14px; margin: 5px 0; }
  .checkbox input { margin-right: 5px; }
  .auto-filled { background: #f0fdf4; }
  @media print { body { padding: 0; } .no-print { display: none; } }
</style>
</head>
<body${autoPrint ? ' onload="window.print()"' : ''}>

<h1>Interatomic Potential Distribution Authorization</h1>
<div class="subtitle">NucPot — Nuclear Material Interatomic Potential Open Platform</div>

<div class="note">
  This authorization confirms that the author or rights holder of an interatomic potential agrees to its public display and distribution on the NucPot platform. Please verify the information, <strong>print, sign, and scan</strong> this document, then submit it when uploading the potential.
</div>

<div class="section-title">1. Potential Information</div>
<table>
  <tr><th>Potential Name</th><td><strong>${esc(p.name)}</strong></td></tr>
  <tr><th>Potential Type</th><td>${esc(p.type)}</td></tr>
  <tr><th>Elements</th><td>${esc(p.elements)}</td></tr>
  <tr><th>System Name</th><td>${esc(p.systemName)}</td></tr>
  <tr><th>Related DOI</th><td>${esc(p.doiRefs) || '(if applicable)'}</td></tr>
</table>

<div class="section-title">2. Grant of Authorization</div>
<ol>
  <li>The Authorizer grants NucPot permission to <strong>publicly display and distribute</strong> the potential for <strong>non-commercial, academic research</strong> purposes.</li>
  <li>Platform users may <strong>freely access</strong> the potential for academic research and educational activities.</li>
  <li>NucPot will <strong>clearly attribute</strong> the original author(s) and citation information on the potential's page.</li>
  <li>This authorization <strong>does not transfer ownership</strong>. The Authorizer retains all intellectual property rights to the potential.</li>
</ol>

<div class="section-title">3. Authorizer's Declarations</div>
<div class="checkbox"><input type="checkbox" id="c1"><label for="c1">I confirm that I am the author of this potential or have obtained lawful authorization</label></div>
<div class="checkbox"><input type="checkbox" id="c2"><label for="c2">This potential does not infringe upon any third party's intellectual property rights</label></div>
<div class="checkbox"><input type="checkbox" id="c3"><label for="c3">I have the legal authority to grant this authorization</label></div>

<div class="section-title">4. Duration</div>
<p style="font-size:14px;">This authorization takes effect upon signing and remains valid until the Authorizer revokes it in writing.</p>

<div class="section-title">5. Authorizer Information</div>
<table>
  <tr><th>Full Name</th><td class="auto-filled"><strong>${esc(u.name)}</strong></td></tr>
  <tr><th>Affiliation</th><td class="auto-filled">${esc(u.affiliation) || ''}</td></tr>
  <tr><th>Title / Position</th><td class="auto-filled">${esc(u.title) || ''}</td></tr>
  <tr><th>Email</th><td class="auto-filled">${esc(u.email)}</td></tr>
  <tr><th>Phone</th><td class="auto-filled">${esc(u.phone) || ''}</td></tr>
  <tr><th>Date</th><td>${today}</td></tr>
</table>

<div class="sig-area">
  <div class="sig-row">Signature: <span class="sig-line"></span></div>
  <div class="sig-row">Date: <span class="sig-line"></span></div>
  <div style="margin-top:10px; font-size:12px; color:#888;">(An institutional seal may be affixed alongside the signature if required.)</div>
</div>

<div class="footer">
  NucPot reserves the right to review and decline submissions. Contact the platform administrator for questions.<br>
  Document generated: ${today}
</div>

</body>
</html>`
}
