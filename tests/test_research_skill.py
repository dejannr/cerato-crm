import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend import generator

class ResearchSkillTests(unittest.TestCase):
    def test_crm_research_job_receives_the_project_skill(self):
        captured={}
        def run(args,**kwargs):
            captured.update(kwargs);captured['args']=args
            Path(args[args.index('-o')+1]).write_text(json.dumps({'first_name':'Alex','subject':'Question','personalized_sentence':'How do you coordinate bookings?','research':'No company details were verified.','sources':[],'criteria':{key:{'score':0,'reason':'Not verified'} for key in generator.CRITERIA},'caveats':'No reliable source'}))
            return SimpleNamespace(returncode=0,stderr='')
        with patch.object(generator.shutil,'which',return_value='/usr/bin/codex'),patch.object(generator.subprocess,'run',side_effect=run):
            result=generator.generate({'company':'Example','research':'Previous source reports uncertain status','research_sources':'https://example.com/old'}, {'subject':'','body':'Hi {{first_name}}'}, 'stefan')
        self.assertEqual(result['caveats'],'No reliable source')
        self.assertEqual(set(result['criteria']),set(generator.CRITERIA))
        self.assertIn(generator.RESEARCH_SKILL.read_text(encoding='utf-8'),captured['input'])
        self.assertIn('Previous source reports uncertain status',captured['input'])
        self.assertIn('a question is not mandatory',captured['input'])
        self.assertIn('--search',captured['args'])
        self.assertIn('--disable',captured['args'])
    def test_missing_skill_stops_generation(self):
        with patch.object(generator.shutil,'which',return_value='/usr/bin/codex'),patch.object(generator,'RESEARCH_SKILL',Path('/no-such-skill/SKILL.md')):
            with self.assertRaisesRegex(RuntimeError,'research skill is missing'): generator.generate({}, {'subject':'','body':''}, 'stefan')
