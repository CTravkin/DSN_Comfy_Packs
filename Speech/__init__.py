from comfy_api.latest import ComfyExtension

from .nodes import NODES

WEB_DIRECTORY = './web'


class SpeechExtension(ComfyExtension):
    async def get_node_list(self):
        return NODES


async def comfy_entrypoint():
    from .library import install
    install()
    return SpeechExtension()
