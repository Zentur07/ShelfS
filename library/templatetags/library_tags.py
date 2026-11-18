from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def querystring_override(context, key, value):
    params = context['request'].GET.copy()
    params[key] = str(value)
    return params.urlencode()
