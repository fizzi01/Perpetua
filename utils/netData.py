from .netConstants import *


# Description: This file contains the functions that are used to format the data that is sent over the network.
def format_data(data: str):
    # if '\n' in data:
    #     data = data.replace('\n', NEWLINE)
    #if '\t' in data:
    #    data = data.replace('\t', TAB)
    #if ' ' in data:
    #    data = data.replace(' ', SPACE)
    return data


def format_data_reverse(data: str):
    if NEWLINE in data:
        data = data.replace(NEWLINE, '\n')
    if TAB in data:
        data = data.replace(TAB, '\t')
    if SPACE in data:
        data = data.replace(SPACE, ' ')

    return data


def format_command(command: str):
    return command.replace(" ", CMD)


def extract_command_parts(command: str):
    return command.split(CMD)


def extract_text(part: str):
    return format_data_reverse(part)
