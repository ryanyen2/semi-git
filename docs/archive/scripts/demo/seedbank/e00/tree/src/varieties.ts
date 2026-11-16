export type Variety = {
  id: string
  name: string
  species: string
  family: string
  daysToHarvest: number
}

export const VARIETIES: Variety[] = [
  { id: 'cherokee-purple-tomato', name: 'Cherokee Purple Tomato', species: 'Solanum lycopersicum', family: 'Nightshade', daysToHarvest: 80 },
  { id: 'dragon-tongue-bean', name: 'Dragon Tongue Bean', species: 'Phaseolus vulgaris', family: 'Legume', daysToHarvest: 60 },
  { id: 'black-beauty-zucchini', name: 'Black Beauty Zucchini', species: 'Cucurbita pepo', family: 'Gourd', daysToHarvest: 50 },
  { id: 'chioggia-beet', name: 'Chioggia Beet', species: 'Beta vulgaris', family: 'Amaranth', daysToHarvest: 55 },
  { id: 'lacinato-kale', name: 'Lacinato Kale', species: 'Brassica oleracea', family: 'Brassica', daysToHarvest: 60 },
  { id: 'moon-and-stars-watermelon', name: 'Moon and Stars Watermelon', species: 'Citrullus lanatus', family: 'Gourd', daysToHarvest: 95 },
  { id: 'golden-bantam-corn', name: 'Golden Bantam Corn', species: 'Zea mays', family: 'Grass', daysToHarvest: 75 },
  { id: 'scarlet-runner-bean', name: 'Scarlet Runner Bean', species: 'Phaseolus coccineus', family: 'Legume', daysToHarvest: 70 },
  { id: 'purple-top-turnip', name: 'Purple Top Turnip', species: 'Brassica rapa', family: 'Brassica', daysToHarvest: 50 },
  { id: 'bloomsdale-spinach', name: 'Bloomsdale Spinach', species: 'Spinacia oleracea', family: 'Amaranth', daysToHarvest: 40 },
  { id: 'rouge-dhiver-lettuce', name: "Rouge d'Hiver Lettuce", species: 'Lactuca sativa', family: 'Aster', daysToHarvest: 60 },
  { id: 'marketmore-cucumber', name: 'Marketmore Cucumber', species: 'Cucumis sativus', family: 'Gourd', daysToHarvest: 55 },
  { id: 'sugar-pie-pumpkin', name: 'Sugar Pie Pumpkin', species: 'Cucurbita pepo', family: 'Gourd', daysToHarvest: 100 },
  { id: 'danvers-carrot', name: 'Danvers Carrot', species: 'Daucus carota', family: 'Carrot', daysToHarvest: 70 },
  { id: 'detroit-dark-red-beet', name: 'Detroit Dark Red Beet', species: 'Beta vulgaris', family: 'Amaranth', daysToHarvest: 60 },
  { id: 'genovese-basil', name: 'Genovese Basil', species: 'Ocimum basilicum', family: 'Mint', daysToHarvest: 60 },
  { id: 'amish-paste-tomato', name: 'Amish Paste Tomato', species: 'Solanum lycopersicum', family: 'Nightshade', daysToHarvest: 85 },
  { id: 'jimmy-nardello-pepper', name: 'Jimmy Nardello Pepper', species: 'Capsicum annuum', family: 'Nightshade', daysToHarvest: 90 },
  { id: 'red-russian-kale', name: 'Red Russian Kale', species: 'Brassica oleracea', family: 'Brassica', daysToHarvest: 50 },
  { id: 'painted-mountain-corn', name: 'Painted Mountain Corn', species: 'Zea mays', family: 'Grass', daysToHarvest: 85 },
  { id: 'yellow-pear-tomato', name: 'Yellow Pear Tomato', species: 'Solanum lycopersicum', family: 'Nightshade', daysToHarvest: 78 },
  { id: 'delicata-squash', name: 'Delicata Squash', species: 'Cucurbita pepo', family: 'Gourd', daysToHarvest: 100 },
  { id: 'french-breakfast-radish', name: 'French Breakfast Radish', species: 'Raphanus sativus', family: 'Brassica', daysToHarvest: 25 },
  { id: 'tromboncino-squash', name: 'Tromboncino Squash', species: 'Cucurbita moschata', family: 'Gourd', daysToHarvest: 85 },
]
