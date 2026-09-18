import asyncio
from .db import init_db,SessionLocal
from .models import Subject,Topic,Question
async def seed():
    await init_db()
    async with SessionLocal() as s:
        sub=await s.scalar(__import__('sqlalchemy').select(Subject).where(Subject.name=='O‘zbekiston tarixi'))
        if not sub:
            sub=Subject(name='O‘zbekiston tarixi'); s.add(sub); await s.flush()
        topic=await s.scalar(__import__('sqlalchemy').select(Topic).where(Topic.subject_id==sub.id,Topic.name=='Amir Temur davri'))
        if not topic:
            topic=Topic(subject_id=sub.id,name='Amir Temur davri'); s.add(topic); await s.flush()
        exists=len((await s.scalars(__import__('sqlalchemy').select(Question).where(Question.topic_id==topic.id))).all())
        if not exists:
            for text,a,b,c,d,correct,exp in [
                ('Amir Temur qachon hokimiyatni egallagan?','1360-yil','1370-yil','1380-yil','1390-yil','B','Amir Temur 1370-yilda hokimiyatni qo‘lga kiritgan.'),
                ('Amir Temur davlatining poytaxti qaysi shahar edi?','Buxoro','Samarqand','Toshkent','Termiz','B','Samarqand davlat markaziga aylantirilgan.'),
            ]: s.add(Question(topic_id=topic.id,text=text,option_a=a,option_b=b,option_c=c,option_d=d,correct_option=correct,explanation=exp))
        await s.commit()
    print('Demo data ready.')
if __name__=='__main__': asyncio.run(seed())
