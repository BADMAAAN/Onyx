"""Pure prompt-construction regressions; no providers, audio or desktop calls."""
import json
from unittest import TestCase

from kk.core.brain import ContextBroker, ToolCapabilityRouter
from kk.core.task import AgentTask


def serialized_size(value):
    return len(json.dumps(value, ensure_ascii=False))


def round_messages(index, content, arguments=None):
    return [
        {'role':'assistant','content':'optional narration', 'tool_calls':[
            {'id':str(index),'type':'function','function':{'name':'inspect','arguments':arguments or {}}}]},
        {'role':'tool','tool_call_id':str(index),'tool_name':'inspect','content':content}]


class ContextBudgetReviewTests(TestCase):
    def assert_complete_pairs(self, messages):
        expected = []
        for message in messages:
            if message['role'] == 'assistant' and message.get('tool_calls'):
                self.assertFalse(expected)
                expected = [call['id'] for call in message['tool_calls']]
            elif message['role'] == 'tool':
                self.assertIn(message['tool_call_id'],expected)
                expected.remove(message['tool_call_id'])
            else:
                self.assertFalse(expected)
        self.assertFalse(expected)

    def test_oversized_system_data_cannot_override_total_budget_or_clip_goal(self):
        contract = 'Core safety and action contract.'
        goal = 'Правка: только Firefox; ничего не отправляй.'
        messages = [{'role':'system','content':contract+'\nMemory data: '+('stored fact '*10000)},
                    {'role':'user','content':goal}]
        compact = ContextBroker().compact_rounds(messages,AgentTask(goal),budget=1800,core_contract=contract)
        self.assertEqual(compact[0]['content'],contract)
        self.assertEqual(compact[-1]['content'],goal)
        self.assertLessEqual(serialized_size(compact),1800)

    def test_multiple_tool_results_share_one_bound_and_keep_negative_verification(self):
        task = AgentTask('Проверь и продолжи текущую задачу')
        messages = [{'role':'system','content':'Contract.'},{'role':'user','content':task.goal}]
        for i in range(6):
            messages.extend(round_messages(i,json.dumps({'ok':True,'data':{'description':'pixels '*10000,'verified':False}})))
        compact = ContextBroker().compact_rounds(messages,task,budget=2400)
        self.assertLessEqual(serialized_size(compact),2400)
        self.assert_complete_pairs(compact)
        self.assertEqual([m['tool_call_id'] for m in compact if m['role']=='tool'][-1],'5')
        results = [json.loads(m['content']) for m in compact if m['role']=='tool']
        self.assertTrue(results)
        self.assertTrue(all(result.get('verified') is False or result.get('data',{}).get('verified') is False for result in results))

    def test_tool_arguments_count_toward_budget_even_when_content_empty(self):
        task = AgentTask('Inspect only')
        arguments = {'text':'Do not repeat this payload '*1000}
        messages = [{'role':'system','content':'Contract'},{'role':'user','content':task.goal}]
        messages.extend(round_messages('large','{}',arguments))
        compact = ContextBroker().compact_rounds(messages,task,budget=1000)
        self.assertLessEqual(serialized_size(compact),1000)
        self.assert_complete_pairs(compact)
        self.assertFalse(any(m.get('tool_calls') for m in compact))
        self.assertEqual(messages[2]['tool_calls'][0]['function']['arguments'],arguments)

    def test_incomplete_parallel_round_is_dropped_as_a_whole(self):
        task = AgentTask('Inspect')
        messages = [{'role':'system','content':'Contract'},{'role':'user','content':task.goal},
            {'role':'assistant','tool_calls':[{'id':'a'},{'id':'b'}]},
            {'role':'tool','tool_call_id':'a','content':'first result'},
            {'role':'system','content':'The second result is absent.'}]
        compact = ContextBroker().compact_rounds(messages,task,budget=2000)
        self.assert_complete_pairs(compact)
        self.assertFalse(any(m.get('tool_calls') or m['role']=='tool' for m in compact))

    def test_complete_parallel_round_preserves_every_result(self):
        task = AgentTask('Inspect')
        messages = [{'role':'system','content':'Contract'},{'role':'user','content':task.goal},
            {'role':'assistant','tool_calls':[{'id':'a'},{'id':'b'}]},
            {'role':'tool','tool_call_id':'b','content':'x'*5000},
            {'role':'tool','tool_call_id':'a','content':'y'*5000}]
        compact = ContextBroker().compact_rounds(messages,task,budget=1200)
        self.assert_complete_pairs(compact)
        self.assertEqual({m['tool_call_id'] for m in compact if m['role']=='tool'},{'a','b'})
        self.assertLessEqual(serialized_size(compact),1200)

    def test_current_goal_too_large_is_error_not_partial_instruction(self):
        goal = 'A very long literal user goal '*200
        with self.assertRaisesRegex(ValueError,'complete user request'):
            ContextBroker().compact_rounds([{'role':'system','content':'Contract'}],AgentTask(goal),budget=500)

    def test_retrieved_fact_survives_long_previous_dialogue(self):
        task = AgentTask('Use my saved preference')
        messages = [{'role':'system','content':'Contract\nSaved preference: browser=Firefox; source=user'},
                    {'role':'assistant','content':'Older dialogue '*2000},
                    {'role':'user','content':task.goal}]
        compact = ContextBroker().compact_rounds(messages,task,budget=700,core_contract='Contract')
        self.assertIn('browser=Firefox', json.dumps(compact))
        self.assertLessEqual(serialized_size(compact),700)

    def test_first_huge_schema_does_not_bypass_bound(self):
        schema = {'type':'function','function':{'name':'huge','description':'test',
            'parameters':{'type':'object','properties':{'argument':{'type':'string','enum':['z'*5000]}}}}}
        result = ToolCapabilityRouter().fit([schema],'test',budget=300)
        self.assertEqual(result,[])
        with self.assertRaisesRegex(ValueError,'Requested tool schema'):
            ToolCapabilityRouter().fit([schema],'test',budget=300,requested=['huge'])

    def test_serialized_schema_list_includes_delimiters(self):
        schemas = [{'type':'function','function':{'name':str(i),'parameters':{'type':'object'}}} for i in range(8)]
        bound = sum(serialized_size(schema) for schema in schemas[:2])
        result = ToolCapabilityRouter().fit(schemas,'test',budget=bound)
        self.assertLessEqual(serialized_size(result),bound)
