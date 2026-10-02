from typing import Literal, Optional
from fastapi import APIRouter, Depends, Query
from .. import cache, catalog
from ..deps import require_profile
router=APIRouter(tags=['catalog'])


@router.get('/api/catalog')
def catalogue(profile: str=Depends(require_profile), type: Literal['','movie','series']='', genre: str='',
              year: Optional[int]=Query(None,ge=1800,le=2200),
              availability: Literal['','ready','check_required','unavailable']='',
              sort: Literal['new','year','title','trending','popular']='new',q: str=Query('',max_length=200),mine: bool=False,
              offset: int=Query(0,ge=0),limit: int=Query(20,ge=1,le=50)):
    return catalog.list_items(cache.get(),profile,type,genre,year,availability,sort,q,mine,offset,limit)


@router.get('/api/genres')
def genres(type: Literal['','movie','series']=''):
    snap=cache.get()
    return {'genres':[{'id':k,'name':v} for k,v in catalog.GENRES.items()
        if any((not type or i['type']==type) and v in i.get('genres',[]) for i in snap.items)]}
