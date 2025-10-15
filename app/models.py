"""
数据模型（使用 Pydantic 进行验证）
保持简洁清爽，不过度复杂
"""
from typing import Optional
from pydantic import BaseModel, Field, validator


class ScoreInput(BaseModel):
    """评分输入模型"""
    rater: str = Field(..., min_length=1, max_length=50)
    target: str = Field(..., min_length=1, max_length=50)
    total: float = Field(..., ge=0, le=100, description="总分（0-100）")
    solve: Optional[float] = Field(None, ge=0, le=4, description="解决程度（0-4）")
    logic: Optional[float] = Field(None, ge=0, le=3, description="答辩逻辑性（0-3）")
    analysis: Optional[float] = Field(None, ge=0, le=3, description="分析与总结（0-3）")
    
    @validator('total')
    def round_total(cls, v):
        """总分四舍五入到整数"""
        return round(v)


class GroupInput(BaseModel):
    """组别输入模型"""
    name: str = Field(..., min_length=1, max_length=50)
    scorable: bool = Field(default=True, description="是否可被评分")
    
    @validator('name')
    def clean_name(cls, v):
        """清理组别名称"""
        # 移除危险字符
        dangerous = ['<', '>', '"', "'", ';', '--', '/*', '*/', 'script']
        cleaned = v.strip()
        for char in dangerous:
            cleaned = cleaned.replace(char, '')
        if not cleaned:
            raise ValueError('组别名称不能为空')
        return cleaned


class SessionInput(BaseModel):
    """场次输入模型"""
    name: str = Field(..., min_length=1, max_length=100)
    
    @validator('name')
    def clean_session_name(cls, v):
        """清理场次名称"""
        cleaned = v.strip()
        if not cleaned:
            raise ValueError('场次名称不能为空')
        return cleaned


class ProgressResponse(BaseModel):
    """进度响应模型"""
    total: int = Field(..., ge=0)
    submitted: int = Field(..., ge=0)
    progress: float = Field(..., ge=0, le=100)
    remaining: int = Field(..., ge=0)
    
    @validator('submitted')
    def validate_submitted(cls, v, values):
        """验证已提交数不超过总数"""
        if 'total' in values and v > values['total']:
            raise ValueError('已提交数不能超过总数')
        return v

