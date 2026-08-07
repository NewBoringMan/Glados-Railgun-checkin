import unittest
from scripts.v3_matrix import build_matrix


class MatrixTests(unittest.TestCase):
    def test_filters_and_pauses(self):
        config = {
            'globalPaused': False,
            'accounts': {
                'AAAAAAAAAAAAAAAA': {'enabled': True, 'autoExchange': True},
                'BBBBBBBBBBBBBBBB': {'enabled': False},
                'CCCCCCCCCCCCCCCC': {'enabled': True, 'archived': True},
            },
        }
        matrix = build_matrix(config)
        self.assertEqual(matrix, {'include': [{
            'slot': 1,
            'account_key': 'AAAAAAAAAAAAAAAA',
            'secret_name': 'GLADOS_ACCOUNT_AAAAAAAAAAAAAAAA',
            'auto_exchange': True,
        }]})
        self.assertEqual(build_matrix(config, 'AAAAAAAAAAAAAAAA')['include'][0]['account_key'], 'AAAAAAAAAAAAAAAA')
        config['globalPaused'] = True
        self.assertEqual(build_matrix(config), {'include': []})

    def test_invalid_account_key_is_not_exposed_to_matrix(self):
        config = {'accounts': {'not-safe': {'enabled': True}}}
        self.assertEqual(build_matrix(config), {'include': []})


if __name__ == '__main__':
    unittest.main()
