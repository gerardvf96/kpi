from collections import defaultdict
from typing import Any

from django.core.exceptions import SuspiciousFileOperation

from kpi.deployment_backends.kc_access.storage import default_kobocat_storage
from kpi.utils.log import logging


def unflatten_submission(submission: dict, parent_path: str = '') -> dict:
    """
    Convert a flattened submission dictionary (with "/" separated keys) into
    a nested hierarchical structure.
    
    Example:
        Input:  {"formhub/uuid": "123", "group/field": "value"}
        Output: {"formhub": {"uuid": "123"}, "group": {"field": "value"}}
    
    Keys that start with "_" (like "_id", "_uuid", etc.) are kept at the root level
    and not nested, unless they contain a "/" separator.
    
    Special handling for arrays: if an array contains dictionaries with keys that
    include the parent path as a prefix, those prefixes are stripped from the
    array item keys.
    
    Args:
        submission: A flattened submission dictionary
        parent_path: The parent path prefix to strip from keys (used internally)
        
    Returns:
        A nested dictionary with hierarchical structure
    """
    result = {}
    
    for key, value in submission.items():
        # Strip parent path prefix if present (for array items)
        original_key = key
        if parent_path and key.startswith(parent_path + '/'):
            key = key[len(parent_path) + 1:]  # +1 for the "/"
        
        # Handle nested values recursively (for arrays of objects)
        if isinstance(value, list):
            # Pass the current path so array items can strip their prefixes
            processed_list = []
            for item in value:
                if isinstance(item, dict):
                    # The items in the array might have keys starting with the array's full path
                    # We need to strip that prefix
                    processed_list.append(unflatten_submission(item, original_key))
                else:
                    processed_list.append(item)
            value = processed_list
        elif isinstance(value, dict):
            value = unflatten_submission(value, parent_path)
        
        # Split the key by "/"
        if '/' in key:
            parts = key.split('/')
            current = result
            
            # Navigate/create the nested structure
            for i, part in enumerate(parts[:-1]):
                if part not in current:
                    current[part] = {}
                elif not isinstance(current[part], dict):
                    # If the intermediate key exists but is not a dict,
                    # we need to keep the original structure
                    # This shouldn't typically happen, but handle it gracefully
                    break
                current = current[part]
            else:
                # Set the final value
                current[parts[-1]] = value
        else:
            # No "/" in key, keep it at the root level
            result[key] = value
    
    return result


def get_attachment_filenames_and_xpaths(
    data: dict, attachment_xpaths: list, child_indexes: dict = None
) -> dict:
    """
    Return a dictionary of all valid attachment filenames of a submission mapped
    to their respective XPath.
    """

    return_dict = {}
    for key, value in data.items():

        if not child_indexes:
            child_indexes = defaultdict(int)

        if isinstance(value, list):
            for index, item_list in enumerate(value):
                if isinstance(item_list, dict):
                    # `child_indexes` is mutable and is mutated while descending
                    # in nested groups (i.e. calling this function recursively)
                    # to keep a trace of each (parent) group index
                    child_indexes[key] = index + 1
                    return_dict.update(
                        get_attachment_filenames_and_xpaths(
                            item_list, attachment_xpaths, child_indexes
                        )
                    )

        elif isinstance(value, dict):
            return_dict.update(
                get_attachment_filenames_and_xpaths(value, attachment_xpaths)
            )
        else:
            if key in attachment_xpaths:
                try:
                    value = default_kobocat_storage.get_valid_name(value)
                except SuspiciousFileOperation:
                    logging.error(f'Could not get valid name from {value}')
                    continue
                if child_indexes:
                    # `key` only contains the XPath with groups without any
                    # index. Recreate XPath with correct indexes found in `
                    # child_indexes`
                    for group_name, group_index in child_indexes.items():
                        # Only apply index on the deepest nested group name
                        # `group_name` could be:
                        #   parent_group/nested_group/nested_nested_group
                        group = group_name.split('/')[-1]
                        key = key.replace(group, f'{group}[{group_index}]')

                return_dict[value] = key

    return return_dict
