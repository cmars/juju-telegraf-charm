from charms.reactive import hook
from charms.reactive import RelationBase
from charms.reactive import scopes


class WSGIRequires(RelationBase):
    scope = scopes.UNIT

    @hook('{requires:apache-website}-relation-{joined,changed}')
    def changed(self):
        conv = self.conversation()
        conv.set_state('{relation_name}.available')

    @hook('{requires:apache-website}-relation-{departed,broken}')
    def broken(self):
        conv = self.conversation()
        conv.remove_state('{relation_name}.available')
