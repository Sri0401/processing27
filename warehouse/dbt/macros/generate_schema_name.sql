{#- Use the configured schema name as-is (staging / intermediate / marts / seeds)
    instead of dbt's default "<target>_<custom>" concatenation. These schemas are
    pre-created with role grants in db/init/001_roles_schemas.sh. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name else target.schema }}
{%- endmacro %}
