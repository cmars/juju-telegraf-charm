import json

from charms.reactive import hook
from charms.reactive import RelationBase
from charms.reactive import scopes


class ExecRequires(RelationBase):
    scope = scopes.UNIT

    @hook('{requires:telegraf-exec}-relation-{joined,changed}')
    def changed(self):
        conv = self.conversation()
        conv.set_state('{relation_name}.connected')
        commands_json_dict = conv.get_remote('commands') # list of commands to run
        if commands_json_dict is not None and json.loads(commands_json_dict):
            conv.set_state('{relation_name}.available')

    @hook('{requires:telegraf-exec}-relation-{departed,broken}')
    def broken(self):
        conv = self.conversation()
        conv.remove_state('{relation_name}.connected')
        conv.remove_state('{relation_name}.available')

    def commands(self):
        cmds = []
        for conv in self.conversations():
            commands_json_dict = conv.get_remote('commands') # list of commands dicts
            for cmd_info in json.loads(commands_json_dict):
                commands = cmd_info.pop('commands', []) # list of commands
                if commands is None and 'command' in cmd_info:
                    commands = [cmd_info.pop('command')]
                if not commands:
                    continue
                data_format = cmd_info.pop('data_format') # json, graphite, influx
                cmd = {'commands': commands, 'data_format': data_format}
                # timeout is otional because we have telegraf 0.12.1 around
                cmd['timeout'] = cmd_info.pop('timeout', '5s')
                # by default run_on_this_unit is True, we risk to run the command
                # everywhere than not running it at all
                cmd['run_on_this_unit'] = cmd_info.pop('run_on_this_unit', True)
                cmd.update(cmd_info)
                cmds.append(cmd)
        return cmds
