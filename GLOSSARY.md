# ESL Pricing Service

Publishes what each electronic shelf label in a business's stores should
display, with prices read from Square.

## Labels

**Tag**:
One physical electronic shelf label, identified by a 12-character hex ID and belonging to exactly one store.
_Avoid_: Label (when meaning the device), ESL, device

**Tag Model**:
The hardware model of a Tag, which fixes its screen size and Panel Colours.
_Avoid_: Tag type, hardware type

**Panel Colours**:
The set of ink colours a Tag's display can show (black/white, plus red and/or yellow on some models).
_Avoid_: Colour mode, tag colour

## Layouts

**Layout**:
A named arrangement of Fields on a Tag: which Field fills each region, and in what colour.
_Avoid_: Screen, view, template

**Preset Layout**:
A Layout that ships with the product and cannot be edited by users.
_Avoid_: Default view, built-in template

**Default Preset**:
The Preset Layout a Tag shows when nothing more specific is assigned; it matches the label design used before Layouts existed.
_Avoid_: Standard layout, fallback layout

**Frame**:
The fixed set of regions a Layout places Fields into, designed for one screen size.
_Avoid_: Grid, skeleton, slots

**Custom Layout**:
A Layout created by a business, owned by that business and usable in any of its stores.
_Avoid_: User template, custom screen

**Field**:
One piece of information that can be placed on a Layout, such as item name, price, SKU, barcode or static text.
_Avoid_: Element, attribute, widget

**Layout Assignment**:
The choice of Layout made for a business, a store, or an individual Tag; the most specific one wins.
_Avoid_: Layout setting, layout override
