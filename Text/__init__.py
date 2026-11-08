WEB_DIRECTORY = './web'


async def comfy_entrypoint():
    from comfy_api.latest import ComfyExtension
    from .library import install
    from .nodes import NODES

    install()

    class TextExtension(ComfyExtension):
        async def get_node_list(self):
            return NODES

    return TextExtension()
