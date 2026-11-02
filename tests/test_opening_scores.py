import unittest
from unittest.mock import patch
from backend import main as app

class OpeningScoreTests(unittest.TestCase):
    def create_lead(self):
        with app.Session.begin() as db:
            lead=app.Lead(company='Example Logistics',status='new',contact_name='Alex Smith',person_email='alex@example.com')
            db.add(lead);db.flush();return lead.id
    def result(self, sources):
        return {'ready':False,'first_name':'Alex','subject':'A short question','personalized_sentence':'How do you coordinate bookings across your routes?','research':'An undated operational detail.','sources':sources,'criteria':{key:{'score':5,'reason':'Test reason'} for key in app.CRITERIA},'caveats':'Current operating status is uncertain.'}
    def template(self):
        return {'subject':'','body':'Hi {{first_name}},\n\n{{personalized_sentence}}\n\nI am {{sender_name}}.','sender_name':'Stefan','sender_role':'CTO'}
    def test_weak_research_still_creates_scored_draft_and_marks_edits(self):
        lead_id=self.create_lead()
        with patch.object(app,'generate',return_value=self.result([])): app.run_generation(lead_id,'stefan',{},self.template())
        lead=app.lead(lead_id);self.assertEqual(lead['status'],'review');self.assertEqual(len(lead['messages']),1)
        draft=lead['messages'][0];assessment=draft['opening_assessment']
        self.assertEqual(assessment['score'],49);self.assertEqual(assessment['criteria']['evidence']['score'],0)
        self.assertIn('uncertain',assessment['caveats']);self.assertFalse(assessment['stale']);self.assertIn('How do you',draft['body'])
        app.update_draft(draft['id'],app.DraftInput(lead_id=lead_id,sender='stefan',recipient='alex@example.com',subject=draft['subject'],body='An edited message'))
        self.assertTrue(app.lead(lead_id)['messages'][0]['opening_assessment']['stale'])
        app.delete_draft(draft['id']);self.assertEqual(app.lead(lead_id)['status'],'new')
        with app.Session() as db: self.assertIsNone(db.get(app.OpeningAssessment,draft['id']))
    def test_sourced_opening_and_uncertain_status_do_not_block(self):
        lead_id=self.create_lead()
        with patch.object(app,'generate',return_value=self.result(['https://example.com/source'])): app.run_generation(lead_id,'stefan',{},self.template())
        self.assertEqual(app.generation_status(lead_id)['state'],'completed')
        draft=app.lead(lead_id)['messages'][0];self.assertEqual(draft['opening_assessment']['score'],100)
        self.assertIn('uncertain',draft['opening_assessment']['caveats'])
    def test_generation_receives_previous_research_for_reassessment(self):
        lead_id=self.create_lead()
        with app.Session.begin() as db:
            lead=db.get(app.Lead,lead_id);lead.research='Status is uncertain';lead.research_sources='https://example.com/source'
        try:
            with patch.object(app.JOB_POOL,'submit') as submit:
                app.generate_lead(lead_id,app.GenerateInput(sender='stefan'))
            snapshot=submit.call_args.args[3]
            self.assertEqual(snapshot['research'],'Status is uncertain')
            self.assertEqual(snapshot['research_sources'],'https://example.com/source')
        finally:
            with app.JOB_LOCK: app.JOBS.pop(lead_id,None)
