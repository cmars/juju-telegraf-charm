from charms.reactive import hook
from charms.reactive import RelationBase
from charms.reactive import scopes


class WSGIRequires(RelationBase):
    scope = scopes.UNIT

    @hook('{requires:statistics}-relation-{joined,changed}')
    def changed(self):
        conv = self.conversation()
        conv.set_state('{relation_name}.connected')
        if conv.get_remote('enabled'):
            # this unit's conversation has a port, so
            # it is part of the set of available units
            conv.set_state('{relation_name}.available')

    @hook('{requires:statistics}-relation-{departed,broken}')
    def broken(self):
        conv = self.conversation()
        conv.remove_state('{relation_name}.available')
