import unittest
from backend import main as app

class DeleteDraftTests(unittest.TestCase):
    def make_lead_and_draft(self, status='review', with_history=False):
        with app.Session.begin() as db:
            lead=app.Lead(company='Draft removal test',status=status,person_email='prospect@example.com',research='Research',research_sources='https://example.com')
            db.add(lead);db.flush()
            draft=app.Message(lead_id=lead.id,direction='outbound',status='draft',sender='stefan',recipient='prospect@example.com',subject='Draft',body='Hello')
            db.add(draft)
            if with_history: db.add(app.Message(lead_id=lead.id,direction='outbound',status='sent',sender='stefan',recipient='prospect@example.com',subject='Previous',body='Earlier'))
            db.flush();return lead.id,draft.id
    def test_deleting_last_draft_resets_new_and_generation_status(self):
        lead_id,draft_id=self.make_lead_and_draft()
        with app.JOB_LOCK: app.JOBS[lead_id]={'state':'completed','message':'Draft created'}
        self.assertEqual(app.delete_draft(draft_id)['lead_status'],'new')
        lead=app.lead(lead_id);self.assertEqual(lead['status'],'new');self.assertEqual(lead['messages'],[]);self.assertIsNone(lead['owner']);self.assertEqual(lead['research'],'Research')
        self.assertEqual(app.generation_status(lead_id)['state'],'idle')
    def test_history_and_stopped_status_are_not_reset(self):
        lead_id,draft_id=self.make_lead_and_draft(with_history=True)
        self.assertEqual(app.delete_draft(draft_id)['lead_status'],'review')
        self.assertEqual(app.lead(lead_id)['messages'][0]['status'],'sent')
        stopped_id,stopped_draft=self.make_lead_and_draft(status='do_not_contact')
        self.assertEqual(app.delete_draft(stopped_draft)['lead_status'],'do_not_contact')
