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
            Path(args[args.index('-o')+1]).write_text(json.dumps({'ready':False,'reason':'no reliable source','first_name':'','subject':'','personalized_sentence':'','research':'','sources':[]}))
            return SimpleNamespace(returncode=0,stderr='')
        with patch.object(generator.shutil,'which',return_value='/usr/bin/codex'),patch.object(generator.subprocess,'run',side_effect=run):
            result=generator.generate({'company':'Example'}, {'subject':'','body':'Hi {{first_name}}'}, 'stefan')
        self.assertFalse(result['ready'])
        self.assertIn(generator.RESEARCH_SKILL.read_text(encoding='utf-8'),captured['input'])
        self.assertIn('--search',captured['args'])
        self.assertIn('--disable',captured['args'])
    def test_missing_skill_stops_generation(self):
        with patch.object(generator.shutil,'which',return_value='/usr/bin/codex'),patch.object(generator,'RESEARCH_SKILL',Path('/no-such-skill/SKILL.md')):
            with self.assertRaisesRegex(RuntimeError,'research skill is missing'): generator.generate({}, {'subject':'','body':''}, 'stefan')
