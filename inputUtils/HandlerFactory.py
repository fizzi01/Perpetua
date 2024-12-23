# HandlerFactory.py
from typing import Optional

from inputUtils import ServerMouseListener, ServerKeyboardListener, ServerClipboardListener, ServerMouseController, \
    ServerClipboardController, ClientMouseListener, ClientKeyboardListener, ClientClipboardListener, \
    ClientMouseController, ClientKeyboardController, ClientClipboardController

from utils.Interfaces import IServerContext, IMessageService, IInputListenerFactory, IInputControllerFactory, IEventBus, \
    IClientContext, IHandler


class ServerInputListenerFactory(IInputListenerFactory):
    def create_mouse_listener(self, context: IServerContext, message_service: IMessageService, event_bus: IEventBus,
                              screen_width: int, screen_height: int, screen_threshold: int) -> ServerMouseListener:
        return ServerMouseListener(context=context, message_service=message_service,
                                   event_bus=event_bus,
                                   screen_width=screen_width, screen_height=screen_height,
                                   screen_threshold=screen_threshold)

    def create_keyboard_listener(self, context: IServerContext,
                                 message_service: IMessageService,
                                 event_bus: IEventBus) -> ServerKeyboardListener:
        return ServerKeyboardListener(context=context, message_service=message_service, event_bus=event_bus)

    def create_clipboard_listener(self, context: IServerContext,
                                  message_service: IMessageService,
                                  event_bus: IEventBus) -> ServerClipboardListener:
        return ServerClipboardListener(context=context, message_service=message_service, event_bus=event_bus, )


class ServerInputControllerFactory(IInputControllerFactory):
    def create_mouse_controller(self, context: IServerContext, message_service: IMessageService,
                                screen_width: Optional[int] = None, screen_height: Optional[int] = None,
                                extra_info: Optional[dict] = None):
        return ServerMouseController(context=context)

    def create_keyboard_controller(self, context: IServerContext, message_service: IMessageService):
        return None
        # return ServerKeyboardController(context=context)

    def create_clipboard_controller(self, context: IServerContext, message_service: IMessageService):
        return ServerClipboardController(context=context)


class ClientInputListenerFactory(IInputListenerFactory):

    def create_mouse_listener(self, context: IClientContext, message_service: IMessageService, event_bus: IEventBus,
                              screen_width: int, screen_height: int, screen_threshold: int) -> IHandler:
        return ClientMouseListener(context=context, message_service=message_service,
                                   event_bus=event_bus, screen_width=screen_width,
                                   screen_height=screen_height, screen_threshold=screen_threshold)

    def create_keyboard_listener(self, context: IClientContext, message_service: IMessageService,
                                 event_bus: IEventBus) -> IHandler:
        return ClientKeyboardListener(context=context, message_service=message_service, event_bus=event_bus)

    def create_clipboard_listener(self, context: IClientContext, message_service: IMessageService,
                                  event_bus: IEventBus) -> IHandler:
        return ClientClipboardListener(context=context, message_service=message_service, event_bus=event_bus)


class ClientInputControllerFactory(IInputControllerFactory):
    def create_mouse_controller(self, context: IClientContext, message_service: IMessageService):
        return ClientMouseController(context=context)

    def create_keyboard_controller(self, context: IClientContext, message_service: IMessageService):
        return ClientKeyboardController(context=context)

    def create_clipboard_controller(self, context: IClientContext, message_service: IMessageService):
        return ClientClipboardController(context=context)
