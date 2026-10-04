"""Execute the actual web renderer against fixture outcomes and verify escaping."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from core.appeal_categories import CATEGORIES, LEGACY_CATEGORY


class AppealCategoryWebRenderTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for web renderer execution')
    def test_all_categories_legacy_and_pending_render_without_html_injection(self):
        rows=[dict(id=i,violation_id=i,status=c.decision,reason='Appeal explanation',
            admin_notes='Specific <script>alert(1)</script> explanation',decided_by='Admin <one>',
            decided_at='2026-10-04 08:00:00',submitted_at='2026-10-03 08:00:00',
            decision_category_display=c.label) for i,c in enumerate(CATEGORIES)]
        rows.append(dict(id=99,violation_id=99,status='approved',reason='Legacy',
            admin_notes='Old reason',decision_category_display=LEGACY_CATEGORY))
        rows.append(dict(id=100,violation_id=100,status='pending',reason='Pending',admin_notes='',decision_category_display=''))
        harness=r'''
const fs=require('fs'),vm=require('vm');
const content={innerHTML:''};
const context={document:{querySelector:key=>key==='#content'?content:null,querySelectorAll:()=>[]},setInterval:()=>0};
vm.createContext(context);
let source=fs.readFileSync('web/app.js','utf8').replace('\nstart();','\n');
vm.runInContext(source,context);
vm.runInContext("page='appeals';render("+JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')))+")",context);
process.stdout.write(content.innerHTML);
'''
        result=subprocess.run(['node','-e',harness],input=json.dumps({'_appeals':rows}),text=True,
            capture_output=True,timeout=10,cwd=Path(__file__).resolve().parents[1],check=True)
        html=result.stdout
        for c in CATEGORIES:self.assertIn(c.label,html)
        self.assertIn(LEGACY_CATEGORY,html)
        self.assertIn('Specific &lt;script&gt;alert(1)&lt;/script&gt; explanation',html)
        self.assertIn('Admin &lt;one&gt;',html)
        self.assertNotIn('<script>',html)
        self.assertEqual(html.count('<strong>Decision category:</strong>'),13)


if __name__=='__main__':unittest.main()
