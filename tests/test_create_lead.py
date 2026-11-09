import unittest
import uuid
from fastapi import HTTPException
from backend import main as app

class CreateLeadTests(unittest.TestCase):
    def test_creates_personal_lead_and_rejects_duplicate(self):
        company='Example '+uuid.uuid4().hex
        data=app.LeadCreate(company=company,city=' Belgrade ',contact_name='Stefan Radovanovic',person_email='person@example.com',email_type='personal')
        result=app.create_lead(data)
        self.assertEqual((result['company'],result['city'],result['person_email'],result['email_type'],result['status'],result['owner']),(company,'Belgrade','person@example.com','personal','new',None))
        with self.assertRaises(HTTPException) as exc: app.create_lead(data.model_copy(update={'company':company.upper(),'person_email':'PERSON@example.com'}))
        self.assertEqual(exc.exception.status_code,409)
    def test_rejects_invalid_input(self):
        for data in (app.LeadCreate(company=' ',person_email='x@example.com'),app.LeadCreate(company='Example',person_email='not an email'),app.LeadCreate(company='Example',person_email='x@example.com',email_type='other')):
            with self.assertRaises(HTTPException) as exc: app.create_lead(data)
            self.assertEqual(exc.exception.status_code,400)
