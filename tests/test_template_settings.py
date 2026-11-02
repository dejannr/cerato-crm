import unittest
from backend import main as app

class TemplateSettingsTests(unittest.TestCase):
    def test_existing_template_uses_profile_settings_without_old_footer(self):
        old='Hi {{first_name}},\n\nI’m Stefan from Cerato Systems.\n\nOffer.\n\nBest,\n{{sender_name}}\n{{sender_role}}, Cerato Systems'
        with app.Session.begin() as db:
            template=db.get(app.EmailTemplate,1)
            if template: template.body=old
            else: db.add(app.EmailTemplate(id=1,subject='',body=old))
        app.update_signature_settings('stefan',app.SignatureSettingsInput(name='Alex',title='Director',email='stefan@ceratosystems.com'))
        result=app.get_template('stefan')
        self.assertEqual((result['sender_name'],result['sender_role']),('Alex','Director'))
        self.assertIn('I’m {{sender_name}} from Cerato Systems.',result['body'])
        self.assertTrue(result['body'].endswith('Offer.'))
        with app.Session() as db: self.assertEqual(db.get(app.EmailTemplate,1).body,old)
        app.update_signature_settings('stefan',app.SignatureSettingsInput(name='Stefan',title='CTO',email='stefan@ceratosystems.com'))
    def test_custom_closing_is_preserved(self):
        text='Hello!\n\nThanks,\nCustom team'
        self.assertEqual(app.normalize_legacy_template(text,'stefan'),text)
    def test_template_edits_cannot_override_sender_settings(self):
        app.update_signature_settings('adrija',app.SignatureSettingsInput(name='Andrija',title='CBO',email='andrija@ceratosystems.com'))
        template=app.update_template(app.TemplateInput(subject='',body='Hello {{sender_name}}',sender_name='Wrong'),sender='adrija')
        self.assertEqual(template['sender_name'],'Andrija')
        self.assertNotIn('Best,',template['body'])
